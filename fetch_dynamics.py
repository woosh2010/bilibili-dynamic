#!/usr/bin/env python3
"""获取 bilibili 某博主动态的 JSON 数据（自算 wbi 签名，规避风控）。

与 ../b站自动推送 复用浏览器 curl 的做法不同，本脚本自行计算 wbi 签名
(w_rid/wts)，因此不需要定期从浏览器复制 URL；唯一会过期的是 Cookie。
为防止大量拉取触发风控，内置：
  - 分页（offset 游标，直到 has_more=false 或达到上限）；
  - 请求间隔 + 随机抖动；
  - 风控码 (-412/-799/-509/-352) 指数退避重试；
  - mixin_key 缓存，避免反复打 nav。

用法:
    python3 fetch_dynamics.py <UID> [--pages N] [--interval 2.0] [--out a.json]
    python3 fetch_dynamics.py 3706959876327428 --pages 3

Cookie 来源(按优先级):
    1. --cookie "k=v; k2=v2"
    2. 环境变量 BILI_COOKIE
    3. 当前目录或 ../b站自动推送/.env 中的 BILI_COOKIE
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from wbi import WbiSigner

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
FEED_SPACE_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"

# bilibili 风控相关返回码：遇这些码需退避重试。
RISK_CODES = {-412, -799, -509, -352, -101}  # -101 未登录(可重试一次确认)


def load_env_file(path: Path) -> Dict[str, str]:
    """极简 .env 解析，不依赖 python-dotenv。"""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def resolve_cookie(cli_cookie: Optional[str]) -> str:
    if cli_cookie:
        return cli_cookie
    if os.environ.get("BILI_COOKIE"):
        return os.environ["BILI_COOKIE"]
    candidates = [Path(".env"), Path(__file__).resolve().parent / ".env",
                  Path(__file__).resolve().parent.parent / "b站自动推送" / ".env"]
    for p in candidates:
        env = load_env_file(p)
        if env.get("BILI_COOKIE"):
            return env["BILI_COOKIE"]
    raise SystemExit("未找到 BILI_COOKIE：请用 --cookie 传入，或设置 BILI_COOKIE 环境变量，"
                     "或在 .env 中配置 BILI_COOKIE")


def parse_cookie_pairs(cookie_str: str) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            pairs[k.strip()] = v.strip()
    return pairs


def resolve_uids(cli_uid: Optional[str]) -> List[str]:
    """UID 来源：命令行 > BILI_UIDS(多个,逗号分隔) > BILI_UID(单个) > 默认。
    命令行只给一个 UID 时返回单元素列表。"""
    if cli_uid:
        return [u.strip() for u in cli_uid.split(",") if u.strip()]
    # BILI_UIDS 环境变量（多个）
    if os.environ.get("BILI_UIDS"):
        return [u.strip() for u in os.environ["BILI_UIDS"].split(",") if u.strip()]
    # .env 文件
    for p in [Path(".env"), Path(__file__).resolve().parent / ".env"]:
        env = load_env_file(p)
        if env.get("BILI_UIDS"):
            return [u.strip() for u in env["BILI_UIDS"].split(",") if u.strip()]
        if env.get("BILI_UID"):
            return [env["BILI_UID"].strip()]
    # BILI_UID 环境变量
    if os.environ.get("BILI_UID"):
        return [os.environ["BILI_UID"].strip()]
    return ["3706959876327428"]  # 默认 UID


def _dynamic_text(item: Dict[str, Any]) -> str:
    """从动态 item 提取全部文字内容（不截断，供 AI 分析）。

    依次收集：动态配文(desc.text) + 视频(archive.title/desc) + 图文(opus.title/summary.text)
    + 专栏(article.title/desc) + 通用/直播。返回拼接后的完整文本。
    """
    m = item.get("modules", {}).get("module_dynamic", {}) or {}
    parts: List[str] = []
    desc = (m.get("desc") or {}).get("text") or ""
    if desc.strip():
        parts.append(desc.strip())
    major = m.get("major") or {}
    arc = major.get("archive") or {}
    if arc:
        if arc.get("title"):
            parts.append(f"【视频】{arc['title']}")
        if arc.get("desc") and arc["desc"] != "-":
            parts.append(arc["desc"])
    opus = major.get("opus") or {}
    if opus:
        if opus.get("title"):
            parts.append(f"【图文】{opus['title']}")
        summ = (opus.get("summary") or {}).get("text") or ""
        if summ.strip():
            parts.append(summ.strip())
    article = major.get("article") or {}
    if article:
        if article.get("title"):
            parts.append(f"【专栏】{article['title']}")
        if article.get("desc") and article["desc"] != "-":
            parts.append(article["desc"])
    for k in ("common", "live", "draw"):
        blk = major.get(k) or {}
        if blk.get("title"):
            parts.append(str(blk["title"]))
        if blk.get("desc") and blk["desc"] != "-":
            parts.append(str(blk["desc"]))
        if k == "draw" and blk.get("desc_first"):
            parts.append(str(blk["desc_first"]))
    return "\n".join(parts).strip()


TYPE_LABELS = {
    "DYNAMIC_TYPE_AV": "视频", "DYNAMIC_TYPE_DRAW": "图文", "DYNAMIC_TYPE_WORD": "文字",
    "DYNAMIC_TYPE_ARTICLE": "专栏", "DYNAMIC_TYPE_FORWARD": "转发", "DYNAMIC_TYPE_LIVE_RCMD": "直播",
}


def print_dynamics(items: List[Dict[str, Any]], uid: str) -> None:
    """可读表格输出到 stdout。"""
    print(f"\n{'=' * 64}\n  B站动态  UID={uid}  共 {len(items)} 条\n{'=' * 64}")
    for i, it in enumerate(items, 1):
        m = it.get("modules", {}) or {}
        a = m.get("module_author", {}) or {}
        t = TYPE_LABELS.get(it.get("type", ""), "动态")
        print(f"\n[{i}] [{t}] {a.get('name', '?')}  ·  {a.get('pub_time', '?')}")
        txt = _dynamic_text(it).replace("\n", " ")
        print(f"    {txt[:60]}{'…' if len(txt) > 60 else ''}")
        if a.get("pub_ts"):
            print(f"    https://www.bilibili.com/opus/{it.get('id_str', '')}")


class DynamicsFetcher:
    def __init__(self, cookie_str: str, min_interval: float = 2.0,
                 jitter: float = 2.0, max_retries: int = 10):
        self.cookies = parse_cookie_pairs(cookie_str)
        self.min_interval = min_interval   # 两次请求最小间隔(秒)
        self.jitter = jitter               # 附加随机抖动上限(秒)
        self.max_retries = max_retries     # 含空响应在内最多重试次数
        self.session = requests.Session()
        self.headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "origin": "https://space.bilibili.com",
            "referer": "https://space.bilibili.com/",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": USER_AGENT,
        }
        self.signer = WbiSigner(self.session, self.cookies, self.headers)
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        """限速：保证请求间隔，加随机抖动，降低被风控概率。"""
        elapsed = time.time() - self._last_request_at
        wait = self.min_interval - elapsed + random.uniform(0, self.jitter)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.time()

    def _headers_for_uid(self, uid: str) -> Dict[str, str]:
        h = dict(self.headers)
        h["referer"] = f"https://space.bilibili.com/{uid}/dynamic"
        return h

    def fetch_page(self, uid: str, offset: str = "") -> Dict[str, Any]:
        """拉取单页动态（首页 offset 传空）。

        重试条件（最多 max_retries 次，默认10次后放弃）：
          - 网络异常 / HTTP 412 / 风控码 / 非JSON
          - 空响应软风控：code=0 但 items 为空，且为首页或 has_more=true（疑似限流）
        每次重试重新签名（刷新 wts/w_rid）并退避。"""
        base_params: Dict[str, Any] = {
            "host_mid": uid,
            "timezone_offset": -480,
            "platform": "web",
            "features": "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,"
                        "onlyfansReplyV2,decorationCard,onlyfansEmojiNew",
            "web_location": "333.1387",
        }
        # 首頁不传 offset=""，否则 API 返回 0 条数据
        if offset:
            base_params["offset"] = offset

        last_err: Optional[str] = None
        for attempt in range(1, self.max_retries + 1):
            # 每次重试重新签名，刷新 wts/w_rid。
            params = self.signer.sign(base_params)
            url = f"{FEED_SPACE_URL}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
            self._throttle()
            try:
                resp = self.session.get(
                    url, cookies=self.cookies,
                    headers=self._headers_for_uid(uid), timeout=15)
            except requests.RequestException as e:
                last_err = f"网络异常: {e}"
                self._retry_msg(uid, attempt, last_err); self._backoff(attempt); continue

            if resp.status_code == 412:
                last_err = f"HTTP 412 风控"
                self._retry_msg(uid, attempt, last_err); self._backoff(attempt); continue

            try:
                payload = resp.json()
            except ValueError:
                last_err = f"非 JSON 响应(HTTP {resp.status_code}): {resp.text[:120]}"
                self._retry_msg(uid, attempt, last_err); self._backoff(attempt); continue

            code = payload.get("code")
            if code == 0:
                data = payload.get("data") or {}
                items = data.get("items") or []
                has_more = data.get("has_more")
                # 空响应软风控：首页或 has_more=true 时空数据，疑似限流，重试。
                if not items and (offset == "" or has_more):
                    last_err = "空响应(疑似软风控)"
                    self._retry_msg(uid, attempt, last_err); self._backoff(attempt); continue
                return payload
            if code in RISK_CODES:
                last_err = f"接口风控 code={code} msg={payload.get('message')}"
                self._retry_msg(uid, attempt, last_err); self._backoff(attempt); continue
            # 非风控错误直接返回，交由调用方处理。
            return payload

        raise RuntimeError(f"{uid} 拉取失败，重试 {self.max_retries} 次仍异常: {last_err}")

    def _retry_msg(self, uid: str, attempt: int, err: str) -> None:
        print(f"  ⚠ {uid} 第 {attempt}/{self.max_retries} 次重试（{err}）...",
              file=sys.stderr, flush=True)

    def _backoff(self, attempt: int) -> None:
        # 指数退避封顶 15s + 抖动，避免 10 次重试总耗时过长。
        delay = min(15.0, (2 ** attempt) * 0.5) + random.uniform(0, 1.5)
        time.sleep(delay)

    def fetch_all(self, uid: str, max_pages: int = 5) -> List[Dict[str, Any]]:
        """分页拉取，直到 has_more=false 或达到 max_pages。"""
        all_items: List[Dict[str, Any]] = []
        offset = ""
        for page in range(1, max_pages + 1):
            payload = self.fetch_page(uid, offset)
            data = payload.get("data") or {}
            items = data.get("items") or []
            if not isinstance(items, list):
                items = []
            all_items.extend(items)
            print(f"[{uid}] 第 {page} 页: 获取 {len(items)} 条，累计 {len(all_items)} 条",
                  file=sys.stderr)
            if not data.get("has_more"):
                print(f"[{uid}] 已无更多动态", file=sys.stderr)
                break
            offset = data.get("offset") or ""
            if not offset:
                break
        return all_items


def main() -> None:
    ap = argparse.ArgumentParser(description="获取 bilibili 博主动态(自算 wbi，直接输出，支持多博主)")
    ap.add_argument("uid", nargs="?", help="博主 UID，多个用逗号分隔(不给则用 .env 的 BILI_UIDS/BILI_UID)")
    ap.add_argument("--pages", type=int, default=1, help="每个博主最多翻页数(默认1)")
    ap.add_argument("--interval", type=float, default=2.0, help="请求最小间隔秒(默认2.0)")
    ap.add_argument("--jitter", type=float, default=2.0, help="随机抖动上限秒(默认2.0)")
    ap.add_argument("--cookie", help="Cookie 字符串(优先于 .env)")
    ap.add_argument("--out", help="输出 JSON 文件路径(不给则打印可读表格)")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON 到 stdout")
    ap.add_argument("--raw", action="store_true", help="输出单页原始响应(不解析翻页，仅单博主)")
    args = ap.parse_args()

    uids = resolve_uids(args.uid)
    if args.raw and len(uids) > 1:
        uids = uids[:1]  # --raw 只支持单博主
    cookie = resolve_cookie(args.cookie)
    fetcher = DynamicsFetcher(cookie, min_interval=args.interval, jitter=args.jitter)

    if args.raw:
        print(json.dumps(fetcher.fetch_page(uids[0]), ensure_ascii=False, indent=2))
        return

    all_results: List[Dict[str, Any]] = []
    for i, uid in enumerate(uids, 1):
        if len(uids) > 1:
            print(f"\n{'#' * 64}\n#  [{i}/{len(uids)}] 博主 UID={uid}\n{'#' * 64}", file=sys.stderr)
        try:
            items = fetcher.fetch_all(uid, max_pages=args.pages)
        except RuntimeError as e:
            print(f"\n✗ 放弃 {uid}：重试 {fetcher.max_retries} 次仍失败，跳过该博主。\n  原因: {e}",
                  file=sys.stderr)
            all_results.append({"uid": uid, "count": 0, "items": [], "error": str(e)})
            continue
        all_results.append({"uid": uid, "count": len(items), "items": items})
        if not (args.out or args.json):
            print_dynamics(items, uid)

    if args.out:
        Path(args.out).write_text(
            json.dumps(all_results if len(all_results) > 1 else all_results[0],
                       ensure_ascii=False, indent=2), encoding="utf-8")
        total = sum(r["count"] for r in all_results)
        print(f"\n已写入 {args.out}（{len(all_results)} 个博主，共 {total} 条）", file=sys.stderr)
    elif args.json:
        print(json.dumps(all_results if len(all_results) > 1 else all_results[0],
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
