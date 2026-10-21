#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
toon_parser.py — 麦当劳 MCP `list-nutrition-foods` 返回解析器

背景
----
官方返回是**双层嵌套**的：
    text = "# API Response Information\n...## Original Response\n{...JSON...}"
    JSON["data"] = "[160]{fields}:\n  row\n  row ..."   ← 这一层才是 TOON

网上流传的实战教程都把返回值直接当 TOON 解析，会解析失败。

TOON 行格式
    猪柳麦满分,null,1288,308,16,16,24,781,213
    字段顺序由头部声明：productName,nutritionDescription,energyKj,energyKcal,
              protein,fat,carbohydrate,sodium,calcium

实测（本项目 2026-10-21 拉取）
    ·158~160 条记录
    · 每行恰好 8 个逗号
    · nutritionDescription 全部为 null

但官方**可能随时补上描述文本**，而描述文本里可能包含逗号，
所以不能简单按逗号位置切分。这里用「从右往左定长切分」，
把开头若干个可能含逗号的描述字段留在左侧，从而免疫这个问题。

作者：laoning
"""

from __future__ import annotations

import json
import re
from typing import Any

# 数值字段：位于 TOON 行尾部，数量固定，按从右往左的顺序切分
NUMERIC_TAIL = ["energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"]

# 文本字段：位于头部，数量固定
TEXT_HEAD = ["productName", "nutritionDescription"]

_HEADER_RE = re.compile(r"^\[(\d+)\]\{(.*?)\}:\s*$")
_ORIGINAL_RE = re.compile(r"##\s*Original\s+Response\s*\n+(.*)", re.S)


def _unwrap_mcp_text(text: str) -> dict:
    """从 MCP 返回文本里挖出最内层的 JSON 对象。

    MCP 返回体形如：
        "## Original Response\\n{...}\\n## 展示结果时，..."
    注意 JSON 后面常常还跟着自然语言说明，必须用 raw_decode 只取第一个对象。
    """
    candidates: list[str] = []
    m = _ORIGINAL_RE.search(text)
    if m:
        candidates.append(m.group(1))
    i = text.find('{"success"')
    if i >= 0:
        candidates.append(text[i:])
    candidates.append(text)

    for c in candidates:
        s = c.strip()
        if not s:
            continue
        try:
            return json.JSONDecoder().raw_decode(s)[0]
        except json.JSONDecodeError:
            continue
    raise ValueError("无法从 MCP 返回文本中提取 JSON 对象")


def _to_null(value: str) -> Any:
    """TOON 的空值表示是字面量 null。"""
    v = value.strip()
    if v == "null" or v == "":
        return None
    return v


def parse_toon(toon: str) -> list[dict]:
    """把 TOON 字符串解析成 list[dict]。

    实现要点：不去重、不假设 nutritionDescription 的内容，
    而是从行尾往回取固定 7 个数值字段，剩余部分再切前导文本字段。
    这样即使描述文本含逗号也不会错位。
    """
    lines = [ln for ln in toon.strip().splitlines() if ln.strip()]
    if not lines:
        return []

    m = _HEADER_RE.match(lines[0].strip())
    if not m:
        raise ValueError(f"TOON 头部格式不符：{lines[0][:80]}")

    declared = int(m.group(1))
    fields = [f.strip() for f in m.group(2).split(",")]

    rows: list[dict] = []
    for ln in lines[1:]:
        raw = ln.strip()
        # 去尾部数值：从右往左 7 个
        tail: list[str] = []
        rest = raw
        for _ in NUMERIC_TAIL:
            idx = rest.rfind(",")
            if idx < 0:
                tail = []
                break
            tail.insert(0, rest[idx + 1:])
            rest = rest[:idx]
        if len(tail) != len(NUMERIC_TAIL):
            # 行内逗号数不足，跳过而不是崩掉
            continue

        # 前导文本字段：从左往右按声明顺序切，末位可含逗号
        head_vals: list[str] = []
        cur = rest
        for _ in range(len(fields) - len(NUMERIC_TAIL) - 1):
            idx = cur.find(",")
            if idx < 0:
                break
            head_vals.append(cur[:idx])
            cur = cur[idx + 1:]
        head_vals.append(cur)

        rec: dict[str, Any] = {}
        for name, val in zip(TEXT_HEAD, head_vals):
            rec[name] = _to_null(val)
        for name, val in zip(NUMERIC_TAIL, tail):
            try:
                rec[name] = int(val)
            except ValueError:
                rec[name] = None
        rows.append(rec)

    if declared != len(rows):
        # 不抛异常，但让调用方能发现官方数据变动
        rows.append({"_warning": f"头部声明 {declared} 条，实际解析 {len(rows)} 条"})
    return rows


def load_nutrition(text: str) -> list[dict]:
    """从 MCP tools/call 原始 text 一步到位拿到营养列表。"""
    outer = _unwrap_mcp_text(text)
    if not outer.get("success", True):
        raise RuntimeError(f"接口返回失败：{outer.get('code')} {outer.get('message')}")
    toon = outer.get("data") or ""
    return parse_toon(toon)


def load_nutrition_from_file(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return load_nutrition(f.read())


# ---------------------------------------------------------------- 派生指标

def protein_density(kcal: float, protein: float) -> float:
    """每 100 千卡的蛋白质克数。衡量「蛋白质密度」的核心指标。"""
    if not kcal:
        return 0.0
    return protein / kcal * 100


def sodium_per_100g(sodium_mg: float) -> float:
    """麦当劳中国营养库按「每份」计，钠单位为mg。

    这里只做标注转换，不臆造份量 —— 份量由接口按单品定义。
    """
    return sodium_mg


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "docs/samples/nutrition_raw.txt"
    data = load_nutrition_from_file(path)
    real = [r for r in data if "_warning" not in r]
    print(f"解析 {len(real)} 条记录")
    if len(real) != len(data):
        print("  警告:", data[-1]["_warning"])

    print("\n字段:", list(real[0].keys()))
    print("\n前5 条：")
    for r in real[:5]:
        print(f"  {r['productName']:<16} {r['energyKcal']:>4}kcal "
              f"蛋白{r['protein']:>3}g 钠{r['sodium']:>4}mg "
              f"蛋白密度{protein_density(r['energyKcal'], r['protein']):.1f}g/100kcal")

    print("\n蛋白质密度TOP8：")
    for r in sorted(real, key=lambda x: -protein_density(x["energyKcal"] or 0, x["protein"] or 0))[:8]:
        print(f"  {r['productName']:<22} {r['energyKcal']:>4}kcal "
              f"蛋白{r['protein']:>3}g 密度{protein_density(r['energyKcal'], r['protein']):.1f}")