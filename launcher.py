#!/usr/bin/env python3
"""B站项目启动面板 —— 双击「启动面板.command」运行。

浏览器打开 http://localhost:8084，点按钮启动各功能模块：
  - 动态监控守护、策略看板、博主管理（后台服务，可启停）
  - 拉取动态、分析荐股、测试Bark、安装开机自启（一次性任务）
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"
PID_PATH = PROJECT_DIR / "out" / "launcher_pids.json"
PY = sys.executable
PORT = 8084

LOG_DIR.mkdir(parents=True, exist_ok=True)
(PROJECT_DIR / "out").mkdir(parents=True, exist_ok=True)

# 功能模块定义
MODULES = [
    {"id": "monitor", "name": "动态监控守护", "desc": "每2分钟轮询,新动态Bark推送,失败告警",
     "cmd": [PY, "monitor.py"], "type": "service", "pattern": "monitor.py"},
    {"id": "dashboard", "name": "策略看板", "desc": "AI荐股分析+跟踪看板",
     "cmd": [PY, "analyze_stocks.py", "--serve", "--port", "8082"], "type": "service", "port": 8082},
    {"id": "manage", "name": "博主管理", "desc": "网页增删监控博主",
     "cmd": [PY, "manage.py", "--port", "8083"], "type": "service", "port": 8083},
    {"id": "fetch", "name": "拉取最新动态", "desc": "一次性拉取全部博主动态并打印",
     "cmd": [PY, "fetch_dynamics.py"], "type": "oneshot"},
    {"id": "build_data", "name": "更新观察面板数据", "desc": "拉取最新动态 → 生成面板数据文件",
     "cmd": [PY, "build_data.py"], "type": "oneshot"},
    {"id": "analyze", "name": "分析荐股", "desc": "AI分析全部动态提取股票策略",
     "cmd": [PY, "analyze_stocks.py"], "type": "oneshot"},
    {"id": "testbark", "name": "测试Bark推送", "desc": "发一条测试通知到手机",
     "cmd": [PY, "monitor.py", "--test-bark"], "type": "oneshot"},
    {"id": "view", "name": "📊 观察面板", "desc": "多博主动态聚合可视化面板",
     "cmd": [], "type": "view", "url": "/dashboard.html"},
    {"id": "launchd", "name": "安装开机自启", "desc": "配置monitor开机自动运行(launchd)",
     "cmd": ["bash", "scripts/setup_launchd.sh", "install"], "type": "oneshot"},
    {"id": "uninstall_launchd", "name": "卸载开机自启", "desc": "取消开机自动运行",
     "cmd": ["bash", "scripts/setup_launchd.sh", "uninstall"], "type": "oneshot"},
]


def load_pids() -> dict:
    if PID_PATH.exists():
        try:
            return json.loads(PID_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_pids(pids: dict) -> None:
    PID_PATH.write_text(json.dumps(pids, ensure_ascii=False, indent=2), encoding="utf-8")


def port_in_use(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def pgrep_alive(pattern: str) -> int:
    """返回匹配 pattern 的进程 PID（排除自身 launcher），无则 0。"""
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=3)
        if r.returncode == 0:
            for line in r.stdout.decode().split():
                pid = int(line)
                if pid != os.getpid():
                    return pid
    except Exception:
        pass
    return 0


def module_status(m: dict) -> dict:
    running = False
    pid = 0
    if m.get("port"):
        running = port_in_use(m["port"])
        pid = pgrep_alive(m["pattern"]) if m.get("pattern") else 0
    elif m.get("pattern"):
        pid = pgrep_alive(m["pattern"])
        running = pid > 0
    return {"running": running, "pid": pid}


def start_module(m: dict) -> dict:
    # 服务类：已在跑则不重复启动
    if m["type"] == "service":
        st = module_status(m)
        if st["running"]:
            return {"ok": True, "msg": "已在运行", "pid": st["pid"]}
    log_file = open(LOG_DIR / f"{m['id']}.log", "a", encoding="utf-8")
    # start_new_session=True 让子进程脱离 launcher，launcher 关闭后仍运行
    proc = subprocess.Popen(m["cmd"], cwd=str(PROJECT_DIR), stdout=log_file,
                            stderr=subprocess.STDOUT, start_new_session=True)
    # 记录 pid（服务类）
    if m["type"] == "service":
        pids = load_pids()
        pids[m["id"]] = proc.pid
        save_pids(pids)
    return {"ok": True, "pid": proc.pid, "msg": "已启动"}


def stop_module(m: dict) -> dict:
    killed = []
    # 按 pattern 杀所有相关进程
    pid = pgrep_alive(m.get("pattern") or m["cmd"][-1])
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except Exception:
            pass
    pids = load_pids()
    pids.pop(m["id"], None)
    save_pids(pids)
    return {"ok": bool(killed), "killed": killed}


class H(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_DIR), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.path = "/templates/launcher.html"
            super().do_GET()
        elif path == "/api/modules":
            data = []
            for m in MODULES:
                s = module_status(m)
                data.append({**m, "running": s["running"], "pid": s["pid"]})
            self._json({"modules": data})
        elif path.startswith("/api/log/"):
            mid = path.rsplit("/", 1)[-1]
            self._send_log(mid)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        mid = "".join(qs.get("id", [])).strip()
        m = next((x for x in MODULES if x["id"] == mid), None)
        if not m:
            self._json({"ok": False, "error": "未知模块"}, 400); return
        if parsed.path == "/api/start":
            try:
                r = start_module(m)
                self._json(r)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif parsed.path == "/api/stop":
            self._json(stop_module(m))
        else:
            self.send_response(404); self.end_headers()

    def _send_log(self, mid):
        p = LOG_DIR / f"{mid}.log"
        text = p.read_text(encoding="utf-8", errors="replace")[-4000:] if p.exists() else "(无日志)"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode())

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # 静默


def main():
    # 如果 8084 已被本项目的另一个 launcher 实例占用，直接打开浏览器退出（不报错）
    if port_in_use(PORT):
        existing_pid = pgrep_alive("launcher.py")
        if existing_pid:
            print(f"启动面板已在运行 (PID {existing_pid})，打开浏览器...")
            try:
                webbrowser.open(f"http://localhost:{PORT}")
            except Exception:
                pass
            return
    server = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    url = f"http://localhost:{PORT}"
    print(f"启动面板 → {url}")
    print("按 Ctrl+C 退出（已启动的监控/看板不受影响）")
    # 自动打开浏览器
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n退出启动面板。")
        server.server_close()


if __name__ == "__main__":
    main()
