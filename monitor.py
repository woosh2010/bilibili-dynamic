#!/usr/bin/env python3
"""B站博主动态监控守护。

每 2 分钟轮询各博主最新动态，发现新动态 → Bark 推送全文；拉取失败 → Bark 告警。

用法:
  python3 monitor.py                # 启动监控(前台)
  python3 monitor.py --test-bark    # 测试 Bark 推送
  python3 monitor.py --interval 60  # 自定义轮询间隔(秒)

环境变量(.env):
  BILI_UIDS   监控博主(逗号分隔)
  BILI_COOKIE 登录 Cookie
  BARK_KEY    Bark 推送 key
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

import requests

from fetch_dynamics import (
    DynamicsFetcher, resolve_cookie, _dynamic_text, TYPE_LABELS,
)
from up_manager import resolve_uids as _resolve_ups

PROJECT_DIR = Path(__file__).resolve().parent
STATE_PATH = PROJECT_DIR / "out" / "monitor_state.json"

# Bark 默认 key（可在 .env 用 BARK_KEY 覆盖）
_DEFAULT_BARK_KEY = "f3Rh6RBoznYYK67JJwjahS"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def get_bark_key() -> str:
    return _env("BARK_KEY", _DEFAULT_BARK_KEY)


def bark(title: str, body: str, group: str = "B站动态") -> bool:
    """Bark 推送（用 curl 规避 macOS LibreSSL 的 requests SSL 问题）。body 超长截断到 1900 字。"""
    key = get_bark_key()
    if not key:
        print("⚠ 未配置 BARK_KEY，跳过推送", file=sys.stderr)
        return False
    body = (body or "")[:1900]
    import subprocess
    payload = json.dumps({"title": title, "body": body, "group": group}, ensure_ascii=False)
    try:
        p = subprocess.run(
            ["curl", "-sS", "-m", "10", "-X", "POST",
             "-H", "Content-Type: application/json",
             "-d", payload, f"https://api.day.app/{key}"],
            capture_output=True, timeout=15)
        if p.returncode != 0:
            print(f"Bark curl 失败 rc={p.returncode}: {p.stderr.decode('utf-8','replace')[:120]}",
                  file=sys.stderr)
            return False
        resp = p.stdout.decode("utf-8", "replace")
        ok = '"code":200' in resp or '"code": 200' in resp
        if not ok:
            print(f"Bark 推送返回异常: {resp[:120]}", file=sys.stderr)
        return ok
    except Exception as e:
        print(f"Bark 推送异常: {e}", file=sys.stderr)
        return False


# ── 状态持久化（记录每个博主已见过的动态 ID）──
def load_state() -> Dict[str, List[str]]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: Dict[str, List[str]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 视频简介获取（与 analyze_stocks 一致）──
def get_bvid(item: Dict[str, Any]) -> str:
    major = (item.get("modules", {}).get("module_dynamic", {}) or {}).get("major") or {}
    return str((major.get("archive") or {}).get("bvid") or "")


def fetch_video_desc(fetcher: DynamicsFetcher, bvid: str) -> str:
    if not bvid:
        return ""
    try:
        params = fetcher.signer.sign({"bvid": bvid})
        hdr = dict(fetcher.headers)
        hdr["referer"] = f"https://www.bilibili.com/video/{bvid}"
        r = fetcher.session.get(
            "https://api.bilibili.com/x/web-interface/view",
            params=params, cookies=fetcher.cookies, headers=hdr, timeout=10)
        d = r.json()
        if d.get("code") == 0:
            desc = (d.get("data") or {}).get("desc") or ""
            return desc if desc and desc != "-" else ""
    except Exception:
        pass
    return ""


def build_dynamic_body(item: Dict[str, Any], fetcher: DynamicsFetcher) -> str:
    """构造新动态的推送正文（全文 + 视频简介 + 链接）。"""
    m = item.get("modules", {}) or {}
    a = m.get("module_author", {}) or {}
    pub_time = a.get("pub_time", "?")
    dyn_type = TYPE_LABELS.get(item.get("type", ""), "动态")
    content = _dynamic_text(item)
    bvid = get_bvid(item)
    if bvid:
        vdesc = fetch_video_desc(fetcher, bvid)
        if vdesc:
            content = (content + "\n【视频简介】" + vdesc).strip()
    dyn_id = str(item.get("id_str") or "")
    url = f"https://www.bilibili.com/opus/{dyn_id}"
    return f"⏰ {pub_time}  [{dyn_type}]\n\n{content or '(无文字内容)'}\n\n🔗 {url}"


def check_once(fetcher: DynamicsFetcher, uids: List[str]) -> None:
    """单轮检查：拉取各博主动态，新动态推送，失败告警。"""
    state = load_state()
    now = datetime.now().strftime("%H:%M:%S")

    for uid in uids:
        try:
            items = fetcher.fetch_all(uid, max_pages=1)
        except RuntimeError as e:
            # 拉取失败 → Bark 告警
            bark("⚠️ B站动态获取失败",
                 f"博主 {uid}\n拉取失败：{e}\n请检查 Cookie 是否过期或网络异常。",
                 group="B站告警")
            print(f"[{now}] ✗ {uid} 获取失败已告警: {e}", file=sys.stderr)
            continue

        seen: Set[str] = set(state.get(uid, []))
        new_items = [it for it in items if str(it.get("id_str", "")) not in seen]

        if not seen:
            # 首次记录该博主：只入库不推送，避免一次性推送大量历史
            state[uid] = [str(it.get("id_str", "")) for it in items]
            print(f"[{now}] {uid} 首次记录 {len(items)} 条（不推送历史）", file=sys.stderr)
        elif new_items:
            for it in new_items:
                m = it.get("modules", {}) or {}
                up_name = (m.get("module_author", {}) or {}).get("name", "?")
                body = build_dynamic_body(it, fetcher)
                bark(f"📢 {up_name} 发布了新动态", body)
                print(f"[{now}] ✓ {up_name} 新动态已推送: {body[:50].splitlines()[0] if body else ''}",
                      file=sys.stderr)
            state[uid] = list(seen | {str(it.get("id_str", "")) for it in new_items})
        else:
            print(f"[{now}] {uid} 无新动态", file=sys.stderr)

    save_state(state)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="B站动态监控守护(新动态Bark推送+失败告警)")
    ap.add_argument("--interval", type=int, default=120, help="轮询间隔秒(默认120)")
    ap.add_argument("--test-bark", action="store_true", help="测试 Bark 推送后退出")
    args = ap.parse_args()

    if args.test_bark:
        ok = bark("✅ Bark 测试", "B站动态监控 Bark 推送测试成功！")
        print("Bark 测试推送:", "成功" if ok else "失败")
        return

    cookie = resolve_cookie(None)
    fetcher = DynamicsFetcher(cookie, min_interval=1.5, jitter=1.0)
    uids = _resolve_ups()

    print(f"B站动态监控启动：{len(uids)} 个博主，每 {args.interval}s 轮询", file=sys.stderr)
    print(f"状态文件: {STATE_PATH}", file=sys.stderr)
    # 启动通知（确认 Bark 配置正常）
    bark("✅ B站监控已启动",
         f"监控 {len(uids)} 个博主\n每 {args.interval} 秒检查一次\n新动态自动推送，拉取失败自动告警",
         group="B站告警")

    while True:
        try:
            uids = _resolve_ups()  # 每轮重读，网页增删博主后自动生效
            check_once(fetcher, uids)
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] 监控轮次异常: {e}", file=sys.stderr)
            bark("⚠️ 监控程序异常", str(e)[:300], group="B站告警")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
