"""监控博主管理 —— up_config.json 为数据源，支持网页增删。

up_config.json 结构:
  {"ups": [{"uid": "...", "name": "...", "added_at": "..."}], "updated_at": "..."}
首次加载时若不存在，从 .env 的 BILI_UIDS 拉昵称自动初始化。
monitor.py / fetch_dynamics 均从此文件读取博主列表，网页增删后下轮监控自动生效。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "out" / "up_config.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fetch_up_name(uid: str) -> str:
    """拉取博主昵称（取最新一条动态的 module_author.name）。失败返回 uid。"""
    try:
        from fetch_dynamics import DynamicsFetcher, resolve_cookie
        f = DynamicsFetcher(resolve_cookie(None), min_interval=1.5, jitter=0.8)
        items = f.fetch_all(uid, max_pages=1)
        for it in items:
            m = it.get("modules", {}) or {}
            name = (m.get("module_author", {}) or {}).get("name")
            if name:
                return str(name)
    except Exception:
        pass
    return uid


def load_ups() -> List[Dict[str, Any]]:
    """读取博主列表。首次自动从 BILI_UIDS 初始化。"""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("ups", [])
        except Exception:
            pass
    # 首次：从 BILI_UIDS 初始化（拉昵称）
    from fetch_dynamics import resolve_uids as _resolve
    uids = _resolve(None)
    if not uids:
        return []
    ups = []
    for uid in uids:
        print(f"  初始化博主 {uid} 昵称...", flush=True)
        ups.append({"uid": uid, "name": fetch_up_name(uid), "added_at": _now()})
    save_ups(ups)
    return ups


def save_ups(ups: List[Dict[str, Any]]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps({"ups": ups, "updated_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def resolve_uids() -> List[str]:
    """供 monitor 使用：返回当前配置的博主 UID 列表。"""
    return [u["uid"] for u in load_ups()]


def add_up(uid: str) -> Dict[str, Any]:
    """添加博主（自动拉昵称）。已存在则刷新昵称。返回该博主信息。"""
    uid = str(uid).strip()
    ups = load_ups()
    # 已存在 → 刷新昵称
    for u in ups:
        if u["uid"] == uid:
            u["name"] = fetch_up_name(uid)
            save_ups(ups)
            return u
    # 新增
    name = fetch_up_name(uid)
    up = {"uid": uid, "name": name, "added_at": _now()}
    ups.append(up)
    save_ups(ups)
    return up


def remove_up(uid: str) -> bool:
    uid = str(uid).strip()
    ups = load_ups()
    new = [u for u in ups if u["uid"] != uid]
    if len(new) == len(ups):
        return False
    save_ups(new)
    return True


def refresh_names() -> List[Dict[str, Any]]:
    """重新拉取所有博主昵称。"""
    ups = load_ups()
    for u in ups:
        u["name"] = fetch_up_name(u["uid"])
    save_ups(ups)
    return ups
