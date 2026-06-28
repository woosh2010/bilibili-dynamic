# bilibili-dynamic —— B站博主动态获取

自算 wbi 签名获取 B站博主动态，**直接运行即输出可读结果**，支持一次监控多个博主。

## 快速开始

```bash
pip install requests
python3 fetch_dynamics.py
```

直接输出（序号 / 类型 / 作者 / 发布时间 / 动态文字 / 链接）：

```
================================================================
  B站动态  UID=3706959876327428  共 12 条
================================================================

[1] [视频] 无敌姜神  ·  昨天 18:19
    下周收官之战！本周人均获利40个点？
    https://www.bilibili.com/opus/1218556061006430212

[2] [视频] 无敌姜神  ·  2天前
    夯爆了！3W带粉冲击百万第五十六天...
    https://www.bilibili.com/opus/1218225679239217170
...
```

## 配置（.env）

首次使用前，在 `.env` 配置 Cookie 和要监控的博主：

```ini
# 必填：从浏览器 F12 → Network → 任意请求的 Cookie 整串复制
BILI_COOKIE=buvid3=...; bili_ticket=...; ...

# 监控多个博主（逗号分隔，无参数运行时按此列表循环）
BILI_UIDS=3706959876327428,3690980475668810,3690981155145964

# 或只监控单个博主
BILI_UID=3706959876327428

# 可选：浏览器指纹字段（不填也能取数，填了更稳）
BILI_DM_IMG_STR=...
BILI_DM_IMG_INTER=...
BILI_DEVICE_REQ_JSON=...
```

获取 Cookie：浏览器打开 `space.bilibili.com` 并登录 → F12 → Network → 刷新页面 → 任选一个 `api.bilibili.com` 请求 → 复制完整 Cookie。

## 用法

```bash
# 默认：读 .env 的 BILI_UIDS，循环拉取每个博主，打印可读表格
python3 fetch_dynamics.py

# 临时指定博主（单个或多个逗号分隔，覆盖 .env）
python3 fetch_dynamics.py 3706959876327428
python3 fetch_dynamics.py 3706959876327428,3690980475668810

# 每个博主多翻几页
python3 fetch_dynamics.py --pages 3

# 存成 JSON 文件（多博主时为数组）
python3 fetch_dynamics.py --out dyn.json

# 输出原始 JSON 到终端
python3 fetch_dynamics.py --json

# 单页原始响应（调试用，仅单博主）
python3 fetch_dynamics.py 3706959876327428 --raw
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `uid`（位置参数） | `.env` 的 `BILI_UIDS` | 博主 UID，多个逗号分隔 |
| `--pages` | 1 | 每个博主翻页数 |
| `--out` | 无 | 写 JSON 文件（不给则打印可读表格） |
| `--json` | 关 | 输出原始 JSON 到终端 |
| `--raw` | 关 | 单页原始响应（仅单博主，调试用） |
| `--interval` | 2.0 | 请求最小间隔秒 |
| `--jitter` | 2.0 | 随机抖动上限秒 |
| `--cookie` | `.env` | Cookie 字符串 |

## 原理

B站 `feed/space` 接口需要 wbi 签名（`w_rid` + `wts`）。本脚本自行计算签名，不依赖从浏览器复制的会过期的 URL：

1. `wbi.py`：从 `/x/web-interface/nav` 取密钥 → 固定混淆表重排得 `mixin_key` → `md5(排序后参数 + mixin_key)` 得 `w_rid`
2. `fetch_dynamics.py`：每次请求实时算签名，带 Cookie + 浏览器头，分页拉取直到 `has_more=false`

风控防护：请求间隔 + 随机抖动 + 风控码退避重试 + **空响应自动重试**。

**空响应重试**：B站软风控常返回 `code=0` 但 `items` 为空。脚本检测到首页或 `has_more=true` 时的空响应，会自动重试（每次重新签名 + 退避），最多 10 次仍失败才放弃并提示 `✗ 放弃 UID`，跳过该博主继续下一个。多博主批量时这能把瞬时限流的数据恢复回来。

## 文件

- `wbi.py` —— wbi 签名核心（`WbiSigner`）
- `fetch_dynamics.py` —— 获取脚本（`DynamicsFetcher` + CLI）
- `test.py` —— cookie 调试脚本

## 常见问题

- **取不到数据/风控**：Cookie 里的 `bili_ticket` 有效期较短，从浏览器重新复制 Cookie 更新 `.env` 的 `BILI_COOKIE`。
- **想换博主**：改 `.env` 的 `BILI_UIDS`，无需动命令行。
- **动态类型**：视频/图文/文字/专栏/转发/直播 自动识别标注。
