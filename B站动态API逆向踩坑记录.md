# B站空间动态 API 逆向：踩坑记录与解决方案

## 目标

获取 B站用户空间动态（`space.bilibili.com/{uid}/dynamic`）的原始数据。

**API 端点**: `GET https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space`

---

## 踩坑 1：WBI 签名算法（-412 错误）

### 现象

直接请求 API 返回 HTTP 412 或 `{"code": -412, "message": "request was banned"}`。

### 原因

B站从 2023年3月起引入 **WBI 签名机制**（`w_rid` + `wts`），所有 Web API 请求必须携带签名。

### 解决方案

#### Step 1: 获取每日密钥

```python
# GET https://api.bilibili.com/x/web-interface/nav
# ⚠️ 关键：未登录时 code=-101，但 data.wbi_img 仍然包含密钥！
nav_data = resp.json()
img_key = nav_data["data"]["wbi_img"]["img_url"].split("/")[-1].split(".")[0]
sub_key = nav_data["data"]["wbi_img"]["sub_url"].split("/")[-1].split(".")[0]
```

#### Step 2: 生成 Mixin Key

```python
# 固定重排表，自2023年引入未变（64元素）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2,  53, 8,  23, 32, 15, 50, 10, 31, 58, 3,  45, 35,
    27, 43, 5,  49, 33, 9,  42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7,  16, 24, 55, 40, 61, 26, 17, 0,  1,  60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6,  63, 57, 62, 11, 36, 20, 34, 44, 52,
]

raw_key = img_key + sub_key  # 64 chars
mixin_key = "".join(raw_key[i] for i in MIXIN_KEY_ENC_TAB)[:32]  # 取前32位
```

#### Step 3: 计算 w_rid

```python
import hashlib, time
from urllib.parse import quote

params["wts"] = int(time.time())

# 关键：按 key 排序 → 过滤 !'()* → URL编码 → +mixin_key → MD5
parts = []
for k in sorted(params.keys()):
    v = str(params[k])
    for ch in "!'()*":
        v = v.replace(ch, "")          # ⚠️ 先过滤，再编码
    parts.append(f"{quote(str(k), safe='')}={quote(v, safe='')}")

query_str = "&".join(parts)
params["w_rid"] = hashlib.md5((query_str + mixin_key).encode()).hexdigest()
```

**⚠️ URL 编码注意点**:
- 必须用 `quote(v, safe='')` 而非 `quote_plus`（空格编码为 `%20` 不是 `+`）
- Python `urllib.parse.quote` 等价于 JS 的 `encodeURIComponent`
- `!'()*` 必须先过滤再从值中去除，然后再 URL 编码

---

## 踩坑 2：缺少设备指纹 Cookie（412 HTML 页面）

### 现象

WBI 签名看似正确，但仍然返回 412 的 HTML 风控页面（而非 JSON 格式的 -412）。

### 原因

B站 API 需要 `buvid3` 设备指纹 cookie。直接用 `requests` 请求 API 时没有这个 cookie。

### 解决方案

**必须先访问 `https://www.bilibili.com/` 首页**，让服务器设置 `buvid3` 等 cookie，再带着这些 cookie 去请求 API。

```python
session = requests.Session()
session.get("https://www.bilibili.com/", timeout=10)  # 获取 buvid3
# 之后所有 API 请求都用同一个 session
```

---

## 踩坑 3：首頁传 `offset=""` 导致 0 条数据

### 现象

首次请求（第一页）返回 `code: 0` 但 items 为空数组 `[]`。

### 原因

构建参数时 `"offset": ""` 作为空字符串传给 API。B站后端把空字符串 offset 视为无效分页游标，返回空数据。

### 解决方案

**第一页不要传 `offset` 参数**。只有从第二页开始，才把上一页响应中的 `data.offset` 值带上。

```python
params = {"host_mid": str(uid), "platform": "web", ...}
if offset:  # 仅在非空时传入
    params["offset"] = str(offset)
```

---

## 踩坑 4：连续请求触发 -352 风控

### 现象

前两页正常，第三页返 `{"code": -352, "message": "-352"}`。

### 原因

请求太快触发频率限制。

### 解决方案

1. **每页之间加 1.5 秒延迟**
2. **-352 时自动重试**：等待更长时间（3秒），重新生成 wts 和 w_rid 再请求

```python
if code == -352:
    time.sleep(delay * 2)
    signed = self._wbi_sign(params)  # 重新签名（生成新的 wts）
    resp = session.get(url, params=signed)
```

---

## 踩坑 5：`resp.json()` 返回 `None`

### 现象

偶尔出现 `'NoneType' object has no attribute 'get'`，traceback 指向 `data.get("code")` 或 `data.get("data").get("items")`。

### 原因

B站某些错误响应直接返回 JSON `null`（不是 `{}`），`resp.json()` 返回 Python `None`。例如隐私账号或特殊风控场景。

### 解决方案

**所有 `resp.json()` 调用后加 `or {}`**：

```python
data = resp.json() or {}  # 防止 null

# data["data"] 也可能是 null
page_data = data.get("data")
if page_data is None:
    break  # 隐私账号或空动态，直接结束
```

---

## 踩坑 6：转发动态解析崩溃（`stat` 字段为 null）

### 现象

`parse_dynamic_item` 在处理转发动态（`item.orig`）时崩溃：

```
AttributeError: 'NoneType' object has no attribute 'get'
  at: stat.get("like", {}).get("count", 0)
```

### 原因

B站转发动态（orig）中的 `module_stat.like` 等字段可能直接是 JSON `null`，而不是 `{"count": 0}`。

```json
// 正常动态
"module_stat": {"like": {"count": 218}, "comment": {"count": 329}}

// 某些转发动态中的 orig
"module_stat": {"like": null, "comment": null}
```

Python 的 `dict.get("like", {})` 在 key 存在但值为 `None` 时返回 `None`（而非默认值 `{}`），
因为默认值只在 key **不存在** 时生效。

### 解决方案

```python
# ❌ 错误写法
stat.get("like", {}).get("count", 0)

# ✅ 正确写法：None 也是 falsy，会落到 or {}
(stat.get("like") or {}).get("count", 0)
```

同时对整个解析做防御：

```python
for item in raw:
    try:
        items.append(parse_dynamic_item(item))
    except Exception:
        items.append({"id_str": item.get("id_str"), "error": "解析失败"})
```

---

## 踩坑 7：多线程共享 Session 竞态

### 现象

用 `ThreadPoolExecutor` 并行获取 5 个用户，只有 1 个成功，其余 4 个抛各种异常。

### 原因

`requests.Session` **不是线程安全的**。多线程共享 Session 时：
- Cookie jar 会被并发写入破坏
- 连接池混用
- WBI 密钥缓存竞态

### 解决方案

**放弃多线程，改为顺序获取**。5 个用户，每个 15 页，每页间隔 1.5s，总耗时约 90 秒，完全可以接受。顺序执行反而更稳定，不会因并发请求叠加触发风控。

```python
for uid in uids:
    items = api.get_dynamics(uid, max_pages=15, delay=1.5)
    results[uid] = [parse_dynamic_item(item) for item in items]
    time.sleep(1.5)  # 用户之间也稍作停顿
```

---

## 踩坑 8：major 类型变更（MAJOR_TYPE_DRAW → MAJOR_TYPE_OPUS）

### 现象

图文动态的 `module_dynamic.major.type` 变成了 `MAJOR_TYPE_OPUS`，旧代码只处理 `MAJOR_TYPE_DRAW`。

### 原因

B站更新了动态类型系统，图文帖从 `DRAW` 改名为 `OPUS`。数据结构也不同：
- 旧版 `major.draw.items[].src` → 新版 `major.opus.pics[].url`
- 新版多了 `major.opus.summary.text` 字段

### 解决方案

两种类型都兼容，优先匹配新版。

---

## 最终工作流程（摘要）

```
1. requests.Session() → GET www.bilibili.com → 获得 buvid3
2. GET api.bilibili.com/x/web-interface/nav → 提取 img_key, sub_key
3. img_key + sub_key → MIXIN_TAB 重排 → 前32位 = mixin_key
4. 对每个请求:
   a. 构建 params（第一页不加 offset）
   b. params.wts = int(time.time())
   c. sorted params → filter !'()* → quote → MD5(query + mixin_key) = w_rid
   d. GET api.bilibili.com/.../feed/space?params + w_rid + wts
   e. 提取 data.offset 用于下一页
   f. sleep(1.5s) 避免风控
5. 解析响应: 对每个 item → module_author + module_dynamic + module_stat
6. 防御式编程: resp.json() or {}, (dict.get(k) or {}).get(k2), 逐条 try/except
```
