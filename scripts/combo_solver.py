#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
combo_solver.py — 套餐轮次级双约束求解器

要解决的问题
------------
麦当劳 MCP 的`query-meals` 返回 113 个餐品，但 `list-nutrition-foods`
只提供单品营养数据。实测柏联广场店的名称精确匹配率仅 **17%（19/113）**，
因为菜单里大量是「套餐名」（巨无霸三件套），营养库只有「单品名」（巨无霸）。

不拆轮次 → 只能给单品热量，算不出套餐的真实营养
拆轮次 → 113 个餐品意味着 113 次 `query-meal-detail` 调用，撞 600 次/分钟限流

本模块的解法：**本地先枚举组合，按预算/营养过滤到 Top-K，
只对K 个候选调 MCP 接口。**

关键设计（每一条都对应一个实测发现）
--------------------------------------
1. 价格单位：`query-meals` 返回「元」字符串，`calculate-price` 返回「分」数字。
   本模块内部统一用**分**做整数运算，只在展示层转换。
2. 加价：`query-meal-detail` 的 `choices[].diffPrice` 形如 `"+ ¥1"` / `"- ¥1"`。
   必须解析这个字符串，静态加价不需要调算价接口就能算总价。
3. 脏数据：门店 `businessStartTime=00:00 & businessEndTime=23:59` 占实测 14/30，
   是默认值污染而非真实营业时间。见 `is_real_business_hours`。
4. 距离：`distance` 字段实测不是米（滇池后海返回 2526），经纬度全 0.0，
   **不能用来做就近排序**。见 `Store`。

作者：laoning
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------- 价格处理

_YUAN_RE = re.compile(r"([+\-−]?)\s*¥\s*([0-9]+(?:\.[0-9]+)?)")


def parse_diff_price(diff: str | None) -> int:
    """把 `diffPrice` 字符串解析为**分**。

    实测取值样本：
        "+ ¥0"  -> 0
        "+ ¥2"  -> 200
        "- ¥1"  -> -100
        "+ ¥11" -> 1100
    返回负数表示减价（小杯玉米杯就是 -¥1，唯一减价项）。

    解析失败一律返回 0—— 宁可低估加价，也不能让整个求解崩掉。
    """
    if not diff:
        return 0
    m = _YUAN_RE.search(diff)
    if not m:
        return 0
    sign, num = m.group(1), m.group(2)
    cents = int(round(float(num) * 100))
    if sign in ("-", "−"):
        return -cents
    return cents


def yuan_to_cents(v) -> int | None:
    """`query-meals` 的 currentPrice 是「元」字符串，统���转成分。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(v * 100))
    s = str(v).strip()
    if not s or s in ("null", "None"):
        return None
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return None


def cents_to_yuan(c: int | None) -> str:
    """展示层：分 → 「¥36.50」。"""
    if c is None:
        return "—"
    return f"¥{c / 100:.2f}"


# ---------------------------------------------------------------- 营养模型


@dataclass
class Nutrition:
    """单个单品/ 组合的营养合计。所有数值单位见字段名。"""

    kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carb: float = 0.0
    sodium: float = 0.0
    calcium: float = 0.0

    def add(self, other: "Nutrition", qty: float = 1.0) -> "Nutrition":
        return Nutrition(
            self.kcal + other.kcal * qty,
            self.protein + other.protein * qty,
            self.fat + other.fat * qty,
            self.carb + other.carb * qty,
            self.sodium + other.sodium * qty,
            self.calcium + other.calcium * qty,
        )

    @property
    def protein_density(self) -> float:
        """蛋白质密度：每 100 千卡的蛋白质克数。

        这是本项目的核心指标之一。实测最高项：
        蒜了吧黄油风味薄皮脆汁鸡-带骨里脊 126kcal/19g → 15.1 g/100kcal
        对比巨无霸 500+ kcal → 不足 3 g/100kcal
        """
        return self.protein / self.kcal * 100 if self.kcal else 0.0

    @property
    def protein_per_yuan(self) -> float:
        """每元钱蛋白质克数。

        **本方法在Nutrition 层不可用** —— Nutrition 不知道价格。
        真正使用请用 `Candidate.protein_per_yuan`。
        """
        return 0.0

    def to_dict(self) -> dict:
        return {
            "kcal": round(self.kcal, 1),
            "protein_g": round(self.protein, 1),
            "fat_g": round(self.fat, 1),
            "carb_g": round(self.carb, 1),
            "sodium_mg": round(self.sodium, 1),
            "calcium_mg": round(self.calcium, 1),
            "protein_density": round(self.protein_density, 2),
        }


# ---------------------------------------------------------------- 门店 / 菜单


@dataclass
class Store:
    store_code: str
    name: str
    address: str = ""
    distance_raw: int | None = None
    #: 实测 distance 不是米，经纬度恒为 0.0，**不可用于排序**
    open_time: str = ""
    close_time: str = ""

    @property
    def hours_suspect(self) -> bool:
        """营业时间是否为默认值污染。实测 30 家里 14 家如此。"""
        return self.open_time in ("00:00", "") and self.close_time in ("23:59", "")


def is_real_business_hours(open_time: str, close_time: str) -> bool:
    """判断营业时间是否可信。

    实测污染样本：`00:00`~`23:59`，占昆明 30 家门店的 46.7%。
    反例（真实午市）：圆通山餐厅 `10:00`~`18:00`。

    任何「现在能不能点」的逻辑，都必须先用这个函数剔除脏数据，
    否则会给用户完全错误的推荐。
    """
    return not (open_time in ("00:00", "") and close_time in ("23:59", ""))


@dataclass
class Choice:
    """`query-meal-detail` 的 `choices[]` 元素。"""

    code: str
    name: str
    diff_price_cents: int = 0
    is_default: bool = False
    #: modification.values[].code列表，如巨无霸的 ['100141'(吉士), '100202'(酸黄瓜)...]
    modification_codes: list[str] = field(default_factory=list)

    @property
    def removable(self) -> bool:
        """是否支持去料（去掉某些配料以降低热量/钠）。"""
        return len(self.modification_codes) > 0


@dataclass
class Round:
    """`query-meal-detail` 的 `rounds[]` 元素。"""

    round_id: int
    name: str
    choices: list[Choice]
    min_quantity: int = 1
    max_quantity: int = 1


@dataclass
class Meal:
    """菜单中的一个餐品（单品或套餐）。"""

    code: str
    name: str
    base_price_cents: int | None = None
    #: 套餐才有
    rounds: list[Round] = field(default_factory=list)
    #: 单品的营养数据；套餐为 None，需从轮次累加
    nutrition: Nutrition | None = None
    category: str = ""

    @property
    def is_combo(self) -> bool:
        return len(self.rounds) > 0

    @property
    def combo_space(self) -> int:
        """套餐的组合空间 = 各轮次候选数的乘积。

        实测巨无霸三件套：1 × 7 × 21 = **147 种组合**。
        113 个餐品若全枚举，可能是几十万个组合 —— 这就是必须本地先算的原因。
        """
        space = 1
        for r in self.rounds:
            space *= max(1, len(r.choices))
        return space


# ---------------------------------------------------------------- 求解器


@dataclass
class Candidate:
    """一个完整候选方案（含轮次选择与特制决定）。"""

    meal: Meal
    picks: list[tuple[Round, Choice]]= field(default_factory=list)
    nutrition: Nutrition = field(default_factory=Nutrition)
    price_cents: int = 0
    removed_items: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def protein_per_yuan(self) -> float:
        return self.protein / (self.price_cents / 100) if self.price_cents else 0.0

    @property
    def protein(self) -> float:
        return self.nutrition.protein

    @property
    def kcal(self) -> float:
        return self.nutrition.kcal


class Goal:
    """求解目标与硬约束。

    注意合规：这里的字段名刻意用「Goal / 偏好」而非「减脂 / 增肌」，
    因为官方参赛声明明确写了「输出不构成医疗、营养或其他专业建议」。
    实际定位是「帮你按自己的偏好挑」，不是「帮你制定饮食方案」。
    """

    def __init__(
        self,
        budget_cents: int,
        protein_g: float = 0.0,
        kcal_cap: float | None = None,
        sodium_cap: float | None = None,
        mode: str = "balanced",
    ):
        self.budget_cents = budget_cents
        self.protein_g = protein_g
        self.kcal_cap = kcal_cap
        self.sodium_cap = sodium_cap
        #: balanced | high_protein | low_sodium
        self.mode = mode

    def score(self, c: Candidate) -> float:
        """给候选打分（越高越好）。不同 mode权重不同。"""
        if self.mode == "high_protein":
            return c.protein_per_yuan * 10 + c.nutrition.protein_density
        if self.mode == "low_sodium":
            return -c.nutrition.sodium + c.nutrition.protein * 2
        # balanced：蛋白密度为主，价格惩罚为辅
        return c.nutrition.protein_density - max(0, c.price_cents - self.budget_cents) / 1000

    def feasible(self, c: Candidate) -> bool:
        if c.price_cents > self.budget_cents:
            return False
        if self.kcal_cap is not None and c.kcal > self.kcal_cap:
            return False
        if self.sodium_cap is not None and c.nutrition.sodium > self.sodium_cap:
            return False
        if self.protein_g and c.protein < self.protein_g:
            return False
        return True


class Solver:
    """轮次级双约束求解器。

    用法：
        solver = Solver(nutrition_index)
        cands = solver.solve(meals, goal, top_k=5)

    `nutrition_index` 是 `{餐品名: Nutrition}`，由 toon_parser 提供。
    """

    def __init__(self, nutrition_index: dict[str, Nutrition]):
        self.nx = nutrition_index
        self.stats = {"枚举组合": 0, "MCP调用预估": 0}

    # ---- 名称对齐 ------------------------------------------------------

    def _lookup(self, name: str) -> Nutrition | None:
        """轮次里的单品名 → 营养库。

        实测只有 17% 能精确匹配，套餐名全是「XX三件套」而营养库是「XX」。
        这里做一次去后缀的降级匹配。
        """
        if not name:
            return None
        if name in self.nx:
            return self.nx[name]
        stripped = re.sub(
            r"(套餐|三件套|二件套|四件套|五件套|组合|超值|升级|经典|精选|随心选?|双拼)", "", name
        ).strip()
        stripped = re.sub(r"[（(].*?[)）]", "", stripped).strip()
        if stripped in self.nx:
            return self.nx[stripped]
        # 反向包含
        for k, v in self.nx.items():
            if stripped and (stripped in k or k in stripped) and abs(len(stripped) - len(k)) <= 3:
                return v
        return None

    # ---- 枚举 ----------------------------------------------------------

    def enumerate_combo(self, meal: Meal, goal: Goal) -> list[Candidate]:
        """枚举一个套餐的所有轮次组合，用硬约束过滤，保留评分最好的若干个。

        **不调任何 MCP 接口**，纯本地计算。
        """
        if not meal.is_combo:
            # 单品直接算
            nut = meal.nutrition or self._lookup(meal.name)
            if not nut:
                return []
            c = Candidate(
                meal=meal,
                nutrition=nut,
                price_cents=meal.base_price_cents or 0,
                notes=["单品"],
            )
            self.stats["枚举组合"] += 1
            return [c] if goal.feasible(c) else []

        # 各轮次取 minQuantity 个候选
        per_round: list[list[tuple[Round, Choice]]] = []
        for r in meal.rounds:
            per_round.append([(r, ch) for ch in r.choices])

        results: list[Candidate] = []
        for combo in itertools.product(*per_round):
            self.stats["枚举组合"] += 1
            nut = Nutrition()
            price = meal.base_price_cents or 0
            unresolved: list[str] = []

            for rd, ch in combo:
                price += ch.diff_price_cents
                sub = self._lookup(ch.name)
                if sub is None:
                    unresolved.append(ch.name)
                else:
                    nut = nut.add(sub, rd.min_quantity)

            # 有任何一项查不到营养 → 该组合不可信，丢弃（而不是给假数字）
            if unresolved:
                continue

            c = Candidate(
                meal=meal,
                picks=list(combo),
                nutrition=nut,
                price_cents=price,
            )
            if goal.feasible(c):
                results.append(c)

        results.sort(key=goal.score, reverse=True)
        return results

    # ---- 特制减料 -------------------------------------------------------

    def apply_removal(
        self, c: Candidate, removals: dict[str, list[str]]
    ) -> Candidate:
        """应用「去料」策略。

        实测：巨无霸的 modification 里有吉士、酸黄瓜、洋葱粒等 5 项，
        去掉吉士约省 45 千卡，且 modification 里 price 均为 0（即免费）。

        `removals` 形如 `{"巨无霸": ["吉士", "酸黄瓜"]}`。
        营养扣减基于营养库同名字段的差值，若无法精确扣除则保守返回原值。
        """
        target = removals.get(c.meal.name)
        if not target:
            return c
        new_n = c.nutrition
        removed: list[str] = []
        for rd, ch in c.picks:
            for rm in target:
                if rm in ch.name or any(rm == mv for mv in self._mod_names(ch)):
                    removed.append(f"{ch.name}去掉「{rm}」")
        if not removed:
            return c
        # 保守：不臆造扣减数值，只记录决策，数值留待 MCP 算价确认
        nc = Candidate(
            meal=c.meal,
            picks=c.picks,
            nutrition=new_n,
            price_cents=c.price_cents,
            removed_items=c.removed_items + removed,
            notes=c.notes + [f"应用去料策略：{'、'.join(removed)}"],
        )
        return nc

    def _mod_names(self, ch: Choice) -> list[str]:
        return []

    # ---- 主入口 ---------------------------------------------------------

    def solve(
        self, meals: Iterable[Meal], goal: Goal, top_k: int = 5
    ) -> list[Candidate]:
        """全量求解：枚举 → 过滤 → 排序 → 只对 Top-K 建议调 MCP 验证。

        返回的候选已按分数排序，调用方**只需对前 K 个调 `calculate-price`验证，
        绝不要对全部候选调接口**（限流 600 次/分钟）。
        """
        pool: list[Candidate] = []
        for m in meals:
            pool.extend(self.enumerate_combo(m, goal))
        pool.sort(key=goal.score, reverse=True)

        top = pool[:top_k]
        self.stats["MCP调用预估"] = len(top)
        return top

    def explain(self, c: Candidate, goal: Goal) -> str:
        """生成人类可读的解释，直接用于 Skill 输出。"""
        lines = [
            f"【{c.meal.name}】{cents_to_yuan(c.price_cents)}",
            f"  热量 {c.kcal:.0f} kcal | 蛋白 {c.protein:.1f} g | 钠 {c.nutrition.sodium:.0f} mg",
            f"  蛋白质密度 {c.nutrition.protein_density:.1f} g/100kcal"
            f" | 每元蛋白 {c.protein_per_yuan:.2f} g",
        ]
        if c.picks:
            lines.append("  配置：")
            for rd, ch in c.picks:
                if rd.round_id == 1 and ch.diff_price_cents == 0:
                    continue
                tag = f"{ch.diff_price_cents/100:+.1f}元" if ch.diff_price_cents else "同价"
                lines.append(f"    轮次{rd.round_id} {rd.name} → {ch.name}（{tag}）")
        for n in c.notes:
            lines.append(f"  {n}")
        return "\n".join(lines)


# ---------------------------------------------------------------- 构建辅助


def build_nutrition_index(records: list[dict]) -> dict[str, Nutrition]:
    """把 toon_parser 的输出转成 `{名称: Nutrition}`。"""
    out: dict[str, Nutrition] = {}
    for r in records:
        if "_warning" in r:
            continue
        out[r["productName"]] = Nutrition(
            kcal=r.get("energyKcal") or 0,
            protein=r.get("protein") or 0,
            fat=r.get("fat") or 0,
            carb=r.get("carbohydrate") or 0,
            sodium=r.get("sodium") or 0,
            calcium=r.get("calcium") or 0,
        )
    return out


def meal_from_detail(name: str, code: str, price: str | None, detail: dict) -> Meal:
    """从 `query-meal-detail` 的返回构建 Meal 对象。"""
    rounds: list[Round] = []
    for rd in detail.get("rounds", []) or []:
        choices: list[Choice] = []
        for ch in rd.get("choices", []) or []:
            mods: list[str] = []
            mod = ch.get("modification")
            if isinstance(mod, dict):
                for it in mod.get("items", []) or []:
                    for v in it.get("values", []) or []:
                        mods.append(v.get("code"))
            choices.append(
                Choice(
                    code=ch.get("code"),
                    name=ch.get("name"),
                    diff_price_cents=parse_diff_price(ch.get("diffPrice")),
                    is_default=bool(ch.get("isDefault")),
                    modification_codes=[m for m in mods if m],
                )
            )
        rounds.append(
            Round(
                round_id=rd.get("id"),
                name=rd.get("name"),
                choices=choices,
                min_quantity=rd.get("minQuantity", 1),
                max_quantity=rd.get("maxQuantity", 1),
            )
        )
    return Meal(
        code=code,
        name=name,
        base_price_cents=yuan_to_cents(price),
        rounds=rounds,
    )


if __name__ == "__main__":
    import json
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.toon_parser import load_nutrition_from_file  # type: ignore

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sample = os.path.join(base, "docs", "samples", "nutrition_raw.txt")
    records = load_nutrition_from_file(sample)
    nx = build_nutrition_index(records)
    print(f"营养索引：{len(nx)} 条")

    # 从真实样本构建巨无霸三件套
    detail_path = os.path.join(base, "docs", "samples", "detail_combo.json")
    with open(detail_path, encoding="utf-8") as f:
        detail = json.load(f)
    meal = meal_from_detail("巨无霸三件套", "9900005466", "37.5", detail["data"])

    print(f"\n{meal.name}组合空间：{meal.combo_space} 种")

    goal = Goal(budget_cents=3700, protein_g=25, kcal_cap=700, mode="high_protein")
    solver = Solver(nx)
    cands = solver.solve([meal], goal, top_k=5)

    print(f"\n本地枚举 {solver.stats['枚举组合']} 组合，"
          f"建议仅需调 {solver.stats['MCP调用预估']} 次 MCP 算价\n")
    for k, c in enumerate(cands, 1):
        print(f"--- 候选 {k} ---")
        print(solver.explain(c, goal))
        print()