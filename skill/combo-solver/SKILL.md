---
name: mcd-combo-solver
description: >-
  在麦当劳中国真实菜单里，按你的预算和营养偏好找出最优套餐组合。
  核心能力是「套餐轮次级求解」——把套餐拆成主食/小食/饮料三个轮次，
  在 140 种组合里找出同样的钱能买到更好搭配的那一个。
  当用户提到「麦当劳点什么」「巨无霸三件套怎么配」「麦麦套餐怎么选」
  「减脂期吃麦当劳」「麦当劳 热量」「套餐 搭配 建议」时使用。
---

# MCD Combo Solver

在麦当劳中国的**真实门店菜单**里做套餐轮次级求解。

## ⚠️ 合规声明（必须原样附在每次输出末尾）

> 本工具输出仅供参考，**不构成医疗、营养或其他专业建议**。
> 餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。

依据：官方 `CONTEST_DECLARATION.md` 原文。
措辞上避免「减脂」「增肌」「瘦身」「制定饮食方案」，改用「按你的偏好挑」。

---

## 调用链路（顺序不可颠倒）

```
1. query-nearby-stores      定位门店     searchType 必须为 2
2. query-meals              拉取菜单     得到餐品与价格（单位：元，字符串）
3. query-meal-detail        拆套餐轮次   仅对参与求解的套餐，每个一次
4. list-nutrition-foods      拉营养库     158~160 条，单位见字段名
5. 本地求解                  combo_solver.py   ← 不调接口
6. calculate-price          仅对 Top-K   单位：分（！）
7. 用户确认后才可能 create-order
```

---

## 硬性规则

### 1. `searchType` 必须为 2

`searchType=1` 查的是「我的收藏门店」，**新账号必然返回**
`600050 收藏餐厅列表为空`。这是实测确认的死路。

### 2. `beType` 与 `orderType` 是两套交叉参数

| 参数 | 取值 |
|---|---|
| `orderType` | 1 = 到店｜2 = 外送 |
| `beType` | 1 = 到店取餐｜2 = 麦乐送到家｜5 = 得来速｜6 = 企业团餐 |

`beCode`：得来速(5)/ 外送 / 团餐**必传**，到店自取(1) 不传。

### 3. 价格单位：内部统一用「分」

- `query-meals` → `currentPrice: "26.5"`（**元**，字符串）
- `calculate-price` → `price: 2650`（**分**，数字）

**直接把算价返回展示给用户，价格会变成 100 倍。**

### 4. `roundList` 字段名是 `round`，不是 `roundId`

```json
{"items":[{"productCode":"9900005466","quantity":1,
  "roundList":[{"round":"2","comboItemList":[{"code":"4437","quantity":1}]},
               {"round":"3","comboItemList":[{"code":"3071","quantity":1}]}]}]}
```

- `round` 是**字符串**
- `code` 必须来自 `query-meal-detail` 的 `choices[].code`，**不能猜**
- 字段名写错时**官方不报参数错误，直接返回 500**

### 5. 营业时间脏数据必须剔除

`businessStartTime=00:00 & businessEndTime=23:59` 占实测 14/30（46.7%），
是**数据缺失被填默认值**，不是真全天营业。

反例：`麦当劳昆明圆通山餐厅` 返回 `10:00~18:00`（真实午市餐厅）。

→ 任何「现在能不能点」的判断，先用 `is_real_business_hours()` 过滤。

### 6. `distance` 不能用于排序

实测「昆明滇池后海餐厅」返回 `distance: 2526`（**不是米**），
且经纬度恒为 `0.0`。**禁止用它做「就近推荐」。**

### 7. 限流：600 次/分钟

**绝不能对全部组合调接口。** 实测巨无霸三件套一个套餐就有 140 种组合，
全量调用必然撞限流。必须本地 `combo_solver.py` 预筛到 Top-K。

### 8. 写操作必须用户明确确认

以下工具是**真实写操作**，绝不能自动触发：

`create-order` / `cancel-order` / `mall-create-order`（**扣积分**）/
`draw-lottery`（**消耗积分**）/ `auto-bind-coupons` / `delivery-create-address`

---

## 本地求解脚本

### `scripts/toon_parser.py`

解析 `list-nutrition-foods`。

**该接口返回是双层嵌套**：MCP 文本里 `## Original Response` 之后的 JSON，
其 `data` 字段**内部**才是 TOON 字符串。直接当 TOON 解析会失败。

实测 158~160 条，9 字段，`nutritionDescription` 全为 `null`。

采用「从右往左定长切 7 个数值字段」的解析方式，
可免疫描述文本含逗号导致的错位。

### `scripts/combo_solver.py`

轮次级双约束求解。

```python
from scripts.combo_solver import (
    Goal, Solver, build_nutrition_index, meal_from_detail, parse_diff_price
)

nx = build_nutrition_index(records)          # {名称: Nutrition}
meal = meal_from_detail(name, code, price, detail["data"])

goal = Goal(
    budget_cents=3700,      # 预算（分）
    protein_g=25,# 蛋白目标（g），0 = 不限
    kcal_cap=700,           # 热量上限，None = 不限
    sodium_cap=None,        # 钠上限，None = 不限
    mode="high_protein",    # balanced | high_protein | low_sodium
)

solver = Solver(nx)
cands = solver.solve([meal], goal, top_k=5)
print(solver.stats)          # {'枚举组合': 140, 'MCP调用预估': 5}
print(solver.explain(cands[0], goal))
```

**实测**：巨无霸三件套本地枚举 140 组合 → 仅需 3 次 MCP 算价（压缩 46.7 倍）。

**核心指标**

| 指标 | 含义 |
|---|---|
| `protein_density` | 每 100 千卡的蛋白质克数 |
| `protein_per_yuan` | 每元钱的蛋白质克数 |

实测最高项：`蒜了吧黄油风味薄皮脆汁鸡-带骨里脊`
126 kcal / 19 g 蛋白 → **15.1 g/100kcal**，对比巨无霸不足 3。

---

## 输出格式

```
【巨无霸三件套】¥36.50
  热量 566 kcal | 蛋白 29.0 g | 钠 997 mg
  蛋白质密度 5.1 g/100kcal | 每元蛋白 0.79 g
  配置：
    轮次2 选择套餐内小食 → 小杯玉米杯（-1.0元）
    轮次3 选择套餐内饮料 → 无糖可口可乐中杯（同价）
  同价位还有 2 个候选，热量更高（含糖饮料）

> 本工具输出仅供参考，不构成医疗、营养或其他专业建议。
> 餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。
```

**必须给出同价位横向对比**——这是本工具的价值所在，
不是给一个答案，而是告诉用户「同样条件下这几个方案的差别」。