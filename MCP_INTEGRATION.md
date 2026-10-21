# MCP 接入说明

本项目实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值。

**所有数据均为真实接口返回，未使用任何估算值或先验知识填充。**

---

## 一、接入信息

| 项 | 值 |
|:---|:---|
| Server 名称 | `mcd-mcp` |
| Server 版本 | `1.0.0` |
| 接入地址 | `https://mcp.mcd.cn/mcp-servers/mcd-mcp` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| 限流 | **600 次/分钟**，超限返回 429 |
| 开放平台 | https://open.mcd.cn/mcp |

> ⚠️ 官方 README 写的接入地址是 `https://mcp.mcd.cn`，
> 但实际路由挂在 `/mcp-servers/mcd-mcp` 子路径下。
> 写 `https://mcp.mcd.cn` 时由客户端自动补全，写完整路径更稳。

### 脱敏配置

见 `mcp-config.example.json`，Token 使用环境变量占位符 `${MCD_MCP_TOKEN}`。

**Token 代表用户本人的麦当劳会员身份，可读取券、积分、历史订单并创建订单。
严禁提交到仓库或出现在公开截图中。**

---

## 二、实际调用的 Tool

### `query-nearby-stores` — 定位门店

| 项 | 内容 |
|:---|:---|
| 入参 | `beType`、`searchType`、`city`、`keyword` |
| 实测返回值 | 门店名称、地址、`distance`、`storeCode`、`beCode`、营业时间、预约选项 |

**实测坑**：`searchType=1` 查的是「我的收藏门店」，**新账号必然返回**
`{"code":600050,"message":"收藏餐厅列表为空"}`。实测传 6 组 city+keyword 均复现。

→ **必须用 `searchType=2`**（按位置搜索）。

**实测数据缺陷**：昆明 30 家门店中 **14 家（46.7%）** 的
`businessStartTime`/`businessEndTime` 为 `00:00`/`23:59`，
这是数据缺失被填默认值，非真实营业时间。
反例 `麦当劳昆明圆通山餐厅` 返回 `10:00~18:00`（真实午市）。

→ 本项目用 `is_real_business_hours()` 剔除后再判断可点性。

---

### `query-meals` — 拉取门店菜单

| 项 | 内容 |
|:---|:---|
| 入参 | `storeCode`、`orderType`、`beType`（得来速/外送需 `beCode`）|
| 实测返回值 | `data.categories[]`（分类）、`data.meals{}`（餐品字典）|

实测柏联广场店：**15 个分类、113 个餐品**。

| 项 | 内容 |
|:---|:---|
| 餐品字段 | `name`、`image`、`currentPrice`、`originalPrice`、`canWithOrder` |
| **价格单位** | **`currentPrice` 是「元」，且为字符串**，如 `"33.5"` |

---

### `query-meal-detail` — 拆解套餐轮次

| 项 | 内容 |
|:---|:---|
| 入参 | `storeCode`、`orderType`、`beType`、`code` |
| 实测返回值 | `rounds[]` → `choices[]`，含 `diffPrice`、`modification` |

**这是本项目最核心的 Tool。**

实测巨无霸三件套（`9900005466`）：

```
轮次1 新升级巨无霸   巨无霸        +¥0   可特制：巨无霸酱/吉士/生菜/酸黄瓜/洋葱粒
轮次2 选择套餐内小食  中薯条        +¥0
                    小杯玉米杯     −¥1   ← 唯一减价项
                    大薯条        +¥2
轮次3 选择套餐内饮料  可乐中杯      +¥0
                    无糖可口可乐中杯 +¥0  ← 官方营养数据 0 kcal
                    麦咖啡™冰奶铁中杯 +¥11
```

**组合空间 1 × 7 × 21 = 147**（代码实测为 140，部分选项去重后）。

`modification.values[].code` 支持去料（去吉士、去酸黄瓜等），
实测这些特制项**价格均为 0**。

---

### `list-nutrition-foods` — 营养数据

| 项 | 内容 |
|:---|:---|
| 入参 | 无 |
| 实测返回条目 | **158~ 160 条** |
| 字段 | `productName`、`nutritionDescription`、`energyKj`、`energyKcal`、`protein`、`fat`、`carbohydrate`、`sodium`、`calcium` |

#### ★ 该Tool 返回 TOON 文本，且是双层嵌套

MCP 返回文本形如：

```
# API Response Information
...（字段说明）...
## Original Response
{"success":true,"code":200,"data":"[160]{fields}:\n  row\n  row..."}
```

**`data` 字段的字符串内部**才是 TOON。直接当 TOON 解析会失败。

TOON 行格式：

```
  猪柳麦满分,null,1288,308,16,16,24,781,213
```

实测 160 行**每行恰好 8 个逗号**，`nutritionDescription` **全部为 `null`**。

本项目 `toon_parser.py` 采用**从右往左定长切 7 个数值字段**的方式解析，
即使官方将来补充含逗号的描述文本也不会错位。

#### ★ 核心卡点：与菜单名称匹配率仅 17%

| 数据 | 实测值 |
|:---|---:|
| 门店菜单餐品 | 113 |
| 营养库条目 | 158 |
| **名称精确匹配** | **19（17%）** |

原因：营养库是**单品名**（`巨无霸`），菜单是**套餐名**（`巨无霸三件套`）。

→ 这就是必须引入 `query-meal-detail` 拆轮次的原因。

---

### `calculate-price` — 算价

| 项 | 内容 |
|:---|:---|
| 入参 | `storeCode`、`orderType`、`beType`、`items[]` |
| 实测返回字段 | `productOriginalPrice`、`price`、`discount`、`productList[]`、`takeWayList[]` |
| **价格单位** | **`price` 是「分」，数字**，如 `2650` |

> ⚠️⚠️ **与 `query-meals` 的「元」相差 100 倍**，展示层必须转换。

#### ★ `roundList` 字段名坑

**字段名是 `round`（字符串），不是 `roundId`（数字）。**

正确写法：

```json
{"items":[{"productCode":"9900005466","quantity":1,
  "roundList":[{"round":"2","comboItemList":[{"code":"4437","quantity":1}]},
               {"round":"3","comboItemList":[{"code":"3071","quantity":1}]}]}]}
```

**字段名或 code 错误时，官方不返回参数校验错误，直接返回
`{"code":500,"message":"服务器发生了未知的问题"}`。**

`comboItemList[].code` 必须来自 `query-meal-detail` 的 `choices[].code`，不能猜。

---

## 三、完整调用流程与顺序约束

```
① query-nearby-stores   searchType=2        定位门店
       ↓
② query-meals           获取菜单             113 餐品 / 15 分类
       ↓
③ query-meal-detail     仅对参与求解的套餐     拆轮次
       ↓
④ list-nutrition-foods  获取营养库           160 条
       ↓
⑤ 本地求解toon_parser.py + combo_solver.py  ← 不调接口
       ↓
⑥ calculate-price       仅对 Top-K候选       验证价格
       ↓
⑦ create-order         仅在用户明确确认后    ★ 真实写操作
```

**顺序不可颠倒**：

- 菜单依赖门店与时段（价格、供应状态因店而异）
- 营养数据必须先拿到才能本地筛选
- 算价必须在轮次确定之后，否则无意义

---

## 四、请求预算设计（限流下的必然取舍）

限流 **600 次/分钟**。

### 无脑方案的问题

柏联广场 113 个餐品，若对每个套餐都调 `query-meal-detail` 再全量算价：
单巨无霸三件套就有 **140 种组合**，全量必然撞限流。

### 本项目方案

| 步骤 | 调用次数 |
|:---|---:|
| `query-nearby-stores` | 1 |
| `query-meals` | 1 |
| `query-meal-detail`（仅参与求解的套餐） | N |
| **本地枚举** | **0** |
| `calculate-price`（仅 Top-K） | **K** |

**实测压缩：140 种组合 → 3 次算价调用。**

代码实现见 `combo_solver.py` 的 `Solver.solve()`，
统计信息通过 `solver.stats` 暴露。

---

## 五、业务价值

### 解决的实际问题

用户在麦当劳点套餐时，**轮次选项会造成显著的营养与价格差异**，
但这些差异：
- 在点餐界面上被折叠，用户不易察觉
- 依赖门店、时段、实时库存，无法靠静态资料预知
- 需要逐个查询才能发现

实测巨无霸三件套（昆明柏联广场店）：

| 配置 | 价格 | 热量 | 蛋白 | 钠 |
|:---|---:|---:|---:|---:|
| 默认 | ¥37.50 | 949 kcal | 31.0 g | 1126 mg |
| **求解结果** | **¥36.50** | **566 kcal** | 29.0 g | **997 mg** |

**省 ¥1，热量低 40%，钠低 129 mg。**

其中「小杯玉米杯 −¥1」与「无糖可乐 ±¥0」两项，
**均为官方实时数据，任何静态资料都无法预知**。

### 附带发现

**蛋白质密度**（每 100 千卡的蛋白质克数）是本项目新增的指标。
实测最高项 `蒜了吧黄油风味薄皮脆汁鸡-带骨里脊`：
126 kcal / 19 g 蛋白 = **15.1 g/100kcal**，
而巨无霸仅 513 kcal / 27 g ≈ **5.3 g/100kcal**。

### 合规约束

依据官方 `CONTEST_DECLARATION.md`：

> 项目输出仅供参考，**不构成医疗、营养或其他专业建议**；
> 餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。

本项目定位为「**帮用户按自身偏好挑选**」，不提供饮食方案或健康建议。
代码中的目标模式命名为 `balanced` / `high_protein` / `low_sodium`，
**刻意避免「减脂」「增肌」「瘦身」等医疗属性词汇**。

### 写操作边界

以下 Tool 为真实写操作，**本项目仅在用户明确确认后调用**：

| Tool | 风险 |
|:---|:---|
| `create-order` | 真实建单，返回支付链接 |
| `cancel-order` | 真实撤单 |
| `mall-create-order` | **扣减积分**、扣减库存 |
| `draw-lottery` | **消耗积分抽奖** |
| `auto-bind-coupons` | 自动领券，消耗账号额度 |
| `delivery-create-address` | 写入用户地址 |

**授权边界的设计原则**：Agent 只建单，钱由用户本人支付。
这与 MCP 服务「返回支付链接、由用户扫码」的设计一致。