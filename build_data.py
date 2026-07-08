#!/usr/bin/env python3
"""更新仪表盘数据。

从 B站 API 拉取各博主最新动态，生成 out/timeline.js 供 dashboard.html 使用。
用法:
  python3 build_data.py              # 拉取并生成数据文件
  python3 build_data.py --pages 5    # 每人最多5页
"""

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
OUT_FILE = PROJECT_DIR / "out" / "timeline.js"

from fetch_dynamics import DynamicsFetcher, resolve_cookie, _dynamic_text, TYPE_LABELS
from up_manager import load_ups, resolve_uids


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_item(item: dict, uid: int) -> dict:
    """将原始 API item 转为面板所需格式。"""
    m = item.get("modules", {}) or {}
    author = m.get("module_author", {}) or {}
    dyn = m.get("module_dynamic", {}) or {}
    stat = m.get("module_stat", {}) or {}

    desc = dyn.get("desc") or {}
    major = dyn.get("major") or {}

    # 判断内容类型
    major_type = (major.get("type") or "").upper()
    content = {"type": "raw"}

    if "ARCHIVE" in major_type:
        arc = major.get("archive") or {}
        content = {
            "type": "video",
            "title": arc.get("title") or "",
            "bvid": arc.get("bvid") or "",
            "play": (arc.get("stat") or {}).get("play") or "",
            "danmaku": (arc.get("stat") or {}).get("danmaku") or "",
            "duration": arc.get("duration_text") or "",
            "desc": arc.get("desc") or "",
        }
    elif "OPUS" in major_type or "DRAW" in major_type:
        opus = major.get("opus") or major.get("draw") or {}
        summary = (opus.get("summary") or {}).get("text") or ""
        content = {
            "type": "opus",
            "images": [],
            "title": opus.get("title") or "",
            "summary_text": summary,
            "jump_url": opus.get("jump_url") or f"//www.bilibili.com/opus/{item.get('id_str','')}",
        }
    elif "ARTICLE" in major_type:
        art = major.get("article") or {}
        content = {
            "type": "article",
            "title": art.get("title") or "",
            "summary": art.get("summary") or "",
        }

    return {
        "id_str": item.get("id_str") or "",
        "type": item.get("type") or "",
        "author": {
            "name": author.get("name") or "",
            "mid": author.get("mid") or uid,
            "face": author.get("face") or "",
        },
        "pub_ts": str(author.get("pub_ts") or 0),
        "pub_time": author.get("pub_time") or "",
        "text": (desc.get("text") or "").strip(),
        "content": content,
        "stats": {
            "like": (stat.get("like") or {}).get("count") or 0,
            "comment": (stat.get("comment") or {}).get("count") or 0,
            "forward": (stat.get("forward") or {}).get("count") or 0,
            "coin": (stat.get("coin") or {}).get("count") or 0 if stat.get("coin") else 0,
        },
        "forward": None,
        "_uid": uid,
    }


def main():
    pages = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--pages" else 10
    cookie = resolve_cookie(None)
    ups = load_ups()
    uids = [str(u.get("uid", "")).strip() for u in ups if str(u.get("uid", "")).strip()]
    if not uids:
        uids = resolve_uids()
    names = {str(u.get("uid", "")).strip(): str(u.get("name", "")).strip() for u in ups}

    if not uids:
        print("未配置 BILI_UIDS", file=sys.stderr)
        sys.exit(1)

    fetcher = DynamicsFetcher(cookie, min_interval=1.5, jitter=1.0)
    all_items = []
    refresh_status = {}

    for i, uid in enumerate(uids, 1):
        print(f"[{i}/{len(uids)}] 拉取 UID={uid} ...", file=sys.stderr)
        try:
            raw = fetcher.fetch_all(uid, max_pages=pages)
        except RuntimeError as e:
            print(f"  ✗ 失败: {e}", file=sys.stderr)
            refresh_status[str(uid)] = {
                "uid": str(uid),
                "name": names.get(str(uid), str(uid)),
                "last_refresh_at": _now(),
                "ok": False,
                "count": 0,
                "error": str(e)[:200],
            }
            continue
        parsed = [parse_item(it, int(uid)) for it in raw]
        all_items.extend(parsed)
        display_name = parsed[0]["author"]["name"] if parsed else names.get(str(uid), str(uid))
        refresh_status[str(uid)] = {
            "uid": str(uid),
            "name": display_name,
            "last_refresh_at": _now(),
            "ok": True,
            "count": len(parsed),
            "error": "",
        }
        print(f"  ✓ {len(parsed)} 条", file=sys.stderr)

    # 按时间倒序
    all_items.sort(key=lambda x: int(x.get("pub_ts", 0)), reverse=True)

    # 写入 JS 文件
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "generated_at": _now(),
        "refresh_status": refresh_status,
    }
    js_content = (
        "var TIMELINE_DATA = " + json.dumps(all_items, ensure_ascii=False) + ";\n"
        "var TIMELINE_META = " + json.dumps(meta, ensure_ascii=False) + ";\n"
    )
    OUT_FILE.write_text(js_content, encoding="utf-8")

    print(f"\n✓ 已写入 {OUT_FILE} ({len(all_items)} 条动态, {OUT_FILE.stat().st_size:,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
