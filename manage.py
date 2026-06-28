#!/usr/bin/env python3
"""B站监控博主管理页面 —— 查看/添加/删除监控的博主。

用法: python3 manage.py [--port 8083]
网页: http://localhost:8083
"""
from __future__ import annotations

import argparse
import json
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from up_manager import load_ups, add_up, remove_up, refresh_names

PROJECT_DIR = Path(__file__).resolve().parent


class H(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_DIR), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.path = "/templates/manage.html"
            super().do_GET()
        elif path == "/api/ups":
            self._json({"ups": load_ups()})
        elif path == "/api/state":
            self._json(self._state_info())
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/api/ups/add":
            uid = "".join(qs.get("uid", [])).strip()
            if not uid:
                self._json({"ok": False, "error": "缺少 uid"}, 400); return
            try:
                up = add_up(uid)
                self._json({"ok": True, "up": up})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/ups/remove":
            uid = "".join(qs.get("uid", [])).strip()
            if not uid:
                self._json({"ok": False, "error": "缺少 uid"}, 400); return
            ok = remove_up(uid)
            self._json({"ok": ok})
        elif path == "/api/ups/refresh":
            ups = refresh_names()
            self._json({"ok": True, "ups": ups})
        else:
            self.send_response(404); self.end_headers()

    def _state_info(self):
        """读 monitor 状态：每个博主已记录的动态数 + 最后更新时间。"""
        state_path = PROJECT_DIR / "out" / "monitor_state.json"
        info = {"running": False, "per_up": {}, "updated_at": ""}
        # 检查 monitor 是否在跑（launchd 或前台）
        try:
            r = subprocess.run(["pgrep", "-f", "monitor.py"], capture_output=True, timeout=3)
            info["running"] = r.returncode == 0
        except Exception:
            pass
        if state_path.exists():
            try:
                st = json.loads(state_path.read_text(encoding="utf-8"))
                info["updated_at"] = st.get("_updated_at", "")
                for k, v in st.items():
                    if isinstance(v, list):
                        info["per_up"][k] = len(v)
            except Exception:
                pass
        return info

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and "/api/" in (args[0] if args else ""):
            print(f"[{self.command}] {args[0]}")


def main():
    ap = argparse.ArgumentParser(description="B站监控博主管理页面")
    ap.add_argument("--port", type=int, default=8083, help="端口(默认8083)")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), H)
    print(f"博主管理 → http://localhost:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped."); server.server_close()


if __name__ == "__main__":
    main()
