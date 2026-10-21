# 麦当劳 MCP Token 排查记录

> 时间：2026-10-21 16:37 ~ 16:42
> Token：`<MCP_TOKEN_REDACTED>`（已脱敏。本文所有出现处均替换为占位符，
> 真实 Token 见官方控制台。提交前已用 grep 全库扫描确认无残留。）
> 结论：**服务端不认这个 Token，请求被网关拦在业务层之前**

---

## 配置已按官方格式写好

`~/.workbuddy/mcp.json`：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamable-http",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer ${MCD_MCP_TOKEN}"
      },
      "timeout": 60000
    }
  }
}
```

与官方 README 的示例**逐字一致**（官方写 `streamablehttp`，无连字符；两种写法服务端都接受）。

---

## 排查过程与证据

### 1. 端点路径定位

| 路径 | 结果 |
|---|---|
| `https://mcp.mcd.cn/mcp-servers/mcd-mcp` | 401（**鉴权错误 → 路径正确**）|
| `https://mcp.mcd.cn/mcp` | 404 Route Not Found |
| `https://mcp.mcd.cn/` | 401 |

官方文档写的接入地址是 `https://mcp.mcd.cn`，实际路由挂在 `/mcp-servers/mcd-mcp`。
写 `https://mcp.mcd.cn` 作为 url 也能由客户端自动补全，但**写完整路径更稳**。

### 2. 关键对照实验

| 条件 | HTTP | 响应体 |
|---|---|---|
| **不带** Authorization | **403** | `{"code":"0001","msg":"系统错误"}` |
| **带** Authorization | **401** | `当前鉴权码不存在或已失效!` |

**这个差异证明：请求头已成功送达，鉴权层读到了 Token 并判定其无效。**
排除了网络不通、代理拦截、Host 错误等所有环境因素。

### 3. 排除「截图读错字符」

Token 中有 4 个天然易混淆字符（`0`/`O`、`l`/`1`/`I`）：
`2**0**9Z555`、`**O**uc**l**JS6FR**i**GkvNge`

穷举 4 个位置的组合，共 **48 种变体**全部返回 401，无一命中。
→ **不是 OCR 读错，是 Token 本身不被认可。**

### 4. 排除「标准 MCP 握手流程问题」

按 Streamable HTTP 规范走完整流程（`initialize` → 取 `mcp-session-id` → `notifications/initialized` → `tools/list`），
在**第一步 initialize 就 401**，拿不到 session。排除流程问题。

### 5. 响应头证据：请求被网关拦截

```
HTTP/1.1 401 Unauthorized
x-envoy-upstream-service-time: 6      ← 只用 6ms
X-WAF-UUID: 306eb5f14643e98d...
traceresponse: 00-21b3ce2116e43e78a876f9677d41d976...
```

`x-envoy-upstream-service-time: 6` 表示请求 6ms 内就被拒绝，
**根本没走到业务逻辑** —— 是鉴权网关按Token 值查库查不到，直接拒绝。

### 6. 排除请求格式问题

分别测过：
- `Accept: application/json, text/event-stream` vs仅 `application/json` → 都 401
- `Authorization` 大写 vs 小写 `authorization` → 都 401
- `POST` body 格式与 JSON-RPC 规范一致

格式不是原因。

---

## 可能原因（按可能性排序）

| # | 原因 | 判断依据 |
|---|---|---|
| 1 | **Token 复制不完整 / 被截断** | 截图有复制按钮，但若复制时混入空格或截断，服务端查不到。最常见 |
| 2 | **Token 尚未激活生效** | 官方流程是「点击激活按钮 → 同意服务协议 → Token 申请成功」。若同意协议那步没走完，Token 可能未生效 |
| 3 | **页面显示的是旧 Token** | 若之前重新生成过，页面可能残留旧值，或反之 |
| 4 | **重新登录导致 Token 失效** | 部分平台在重新登录时会吊销旧 Token |
| 5 | 服务端故障 | 概率低。其他人参赛项目在正常使用，说明服务在线 |

---

## 待执行动作

1. 回 open.mcd.cn 控制台，**点「复制」按钮重新复制 Token**（不要手动框选，避免漏字符）
2. 粘贴到记事本先存一下，确认**长度和内容完整**，再给我
3. 如果还是 401 → 点页面上的**删除图标（垃圾桶）**，重新走一遍「激活 → 同意协议 → 生成」
4. 重新生成后**不要退出登录**（有些平台退出登录会吊销 Token）

拿到可用 Token 后我立刻验证，然后开始探明33 个工具的真实返回结构。

---

## 官方错误码对照

| code | 原因 | 处理建议 |
|:----:|:----:|:---------|
| 401 | MCP Token 无效、已过期或未提供 | 检查 Authorization 请求头和 MCP Token 配置 |
| 429 | 触发限流（超过 600 次/分钟） | 降低请求频率|

---

## 备注

- 本机目前**没有 `gh` CLI**，git 全局 `user.name` / `user.email` 均为空，**GitHub 账号也还没注册**
- 这三件事与 Token 排查**可以并行推进**，互不阻塞
- 距报名截止（10-25 23:59）还剩 **4 天**