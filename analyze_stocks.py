#!/usr/bin/env python3
"""B站博主荐股分析 —— 动态 → DeepSeek 提取股票策略 → 策略看板。

用法:
  python3 analyze_stocks.py                    # 拉取动态 → AI 分析 → 存策略库
  python3 analyze_stocks.py --serve            # 启动策略看板(含分析)
  python3 analyze_stocks.py --port 8082        # 自定义看板端口
  python3 analyze_stocks.py --reanalyze        # 强制重新分析全部(不清缓存)
  python3 analyze_stocks.py --uid 3706959876327428  # 仅分析指定博主

工作流:
  1. 从 fetch_dynamics 拉取各博主最新动态
  2. 已分析的动态(analyzed_ids 记录)跳过，新动态交给 DeepSeek
  3. DeepSeek 返回 JSON: {stocks:[{name,code,action,strategy,...}], summary}
  4. 策略存入 out/strategy_picks.json
  5. --serve 时启动策略看板 HTTP 服务
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fetch_dynamics import (
    DynamicsFetcher, resolve_cookie, resolve_uids, print_dynamics,
    _dynamic_text, TYPE_LABELS, USER_AGENT,
)
from deepseek_client import chat, extract_json, get_config

PROJECT_DIR = Path(__file__).resolve().parent
OUT_DIR = PROJECT_DIR / "out"
PICKS_PATH = OUT_DIR / "strategy_picks.json"
TEMPLATE_HTML = PROJECT_DIR / "templates" / "strategy_dashboard.html"

# ── DeepSeek Prompt ──
SYSTEM_PROMPT = """你是A股投资策略分析助手。请仔细分析以下B站博主动态的【全部文字内容】（含视频标题、视频简介、图文正文），
提取博主提到的真实投资内容：

1. 如果提到具体A股股票的操作建议（买入/卖出/持有/关注），提取每只股票的：名称、6位代码、操作方向、
   入场价/止损/目标价（如明确提到）、时间框架、理由。
2. 即使没有具体股票，如果提到板块/题材/行业方向（如"军工"、"光伏"、"AI"），填入 sectors 字段。
3. 用一句话总结博主这条动态的核心观点。

注意：股票代码必须是6位数字（如600118、000725）。不要编造未提及的股票。
只输出 JSON，不要多余文本。"""

ANALYSIS_PROMPT = """动态全部文字内容：
---
{content}
---

请以 JSON 格式返回分析结果：
{{
  "stocks": [
    {{
      "name": "公司名称",
      "code": "6位代码(如600118, 未明确则null)",
      "action": "买入|卖出|持有|关注",
      "entry_price": "建议入场价(如明确提到, 否则null)",
      "stop_loss": "止损价或比例(如明确提到, 否则null)",
      "target": "目标价(如明确提到, 否则null)",
      "timeframe": "短线|中线|长线|未明确",
      "reasoning": "博主给出的理由(摘要, 30字内)",
      "confidence": 0.0-1.0
    }}
  ],
  "sectors": ["提到的板块/题材(如军工/光伏/AI, 没有则空数组)"],
  "summary": "一句话总结博主核心观点(30字内)"
}}"""


def load_picks() -> Dict[str, Any]:
    if PICKS_PATH.exists():
        try:
            return json.loads(PICKS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"analyzed_ids": [], "picks": []}


def save_picks(data: Dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    PICKS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_video_desc(fetcher: DynamicsFetcher, bvid: str) -> str:
    """通过视频详情接口 /x/web-interface/view 获取完整视频简介（需 wbi 签名）。"""
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


def get_dynamic_bvid(item: Dict[str, Any]) -> str:
    """从视频动态提取 bvid。"""
    major = (item.get("modules", {}).get("module_dynamic", {}) or {}).get("major") or {}
    return str((major.get("archive") or {}).get("bvid") or "")


def analyze_dynamic(content: str) -> Optional[Dict[str, Any]]:
    """单条动态 → DeepSeek → 提取的股票建议。无建议返回 None。"""
    if not content.strip():
        return None
    try:
        resp = chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ANALYSIS_PROMPT.format(content=content[:4000])},
        ], temperature=0.1, max_tokens=1500)
    except RuntimeError as e:
        print(f"  ✗ DeepSeek 调用失败: {e}", file=sys.stderr)
        return None

    obj, raw = extract_json(resp)
    if obj is None:
        print(f"  ✗ AI 返回非 JSON: {raw[:120]}", file=sys.stderr)
        return None
    stocks = obj.get("stocks") or []
    sectors = obj.get("sectors") or []
    summary = obj.get("summary", "")
    # 有具体股票 或 有板块题材/非空总结，都算有内容
    if not stocks and not sectors and not summary:
        return None
    return {"stocks": stocks, "sectors": sectors, "summary": summary, "ai_raw": raw}


def run_analysis(uids: List[str], reanalyze: bool = False) -> Dict[str, Any]:
    """拉取动态 → 分析 → 存入策略库。返回策略库 dict。"""
    data = load_picks()
    analyzed_ids: List[str] = data.get("analyzed_ids", [])
    new_count = 0
    cookie = resolve_cookie(None)
    fetcher = DynamicsFetcher(cookie, min_interval=2.0, jitter=1.5)

    for up_i, uid in enumerate(uids, 1):
        print(f"\n{'─' * 56}\n博 主 [{up_i}/{len(uids)}] UID={uid}\n{'─' * 56}", file=sys.stderr)
        try:
            items = fetcher.fetch_all(uid, max_pages=2)
        except RuntimeError as e:
            print(f"  ✗ 拉取失败，跳过: {e}", file=sys.stderr)
            continue

        up_name = ""
        for it in items:
            m = it.get("modules", {}) or {}
            a = m.get("module_author", {}) or {}
            up_name = a.get("name", "") or up_name
            dyn_id = str(it.get("id_str") or it.get("id") or "")

            if not reanalyze and dyn_id in analyzed_ids:
                continue  # 已分析过，跳过

            content = _dynamic_text(it)
            pub_time = a.get("pub_time", "")
            pub_ts = int(a.get("pub_ts") or 0)  # Unix 秒级时间戳，用于精确显示
            dyn_type = TYPE_LABELS.get(it.get("type", ""), "动态")
            # 视频动态：额外拉取视频完整简介（动态标题常很短，简介里才有具体内容）
            bvid = get_dynamic_bvid(it)
            if bvid:
                vdesc = fetch_video_desc(fetcher, bvid)
                if vdesc:
                    content = (content + "\n【视频简介】" + vdesc).strip()
            print(f"  [{dyn_type}] {pub_time} 分析中({len(content)}字)...", end=" ", flush=True)

            result = analyze_dynamic(content)
            if result:
                pick = {
                    "id": f"{uid}-{dyn_id}",
                    "up_uid": uid, "up_name": up_name,
                    "dynamic_id": dyn_id, "dynamic_time": pub_time, "dynamic_ts": pub_ts,
                    "dynamic_url": f"https://www.bilibili.com/opus/{dyn_id}",
                    "dynamic_text": content,
                    "dynamic_type": dyn_type,
                    "stocks": result["stocks"],
                    "sectors": result.get("sectors", []),
                    "summary": result["summary"],
                    "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                data["picks"].insert(0, pick)
                analyzed_ids.append(dyn_id)
                new_count += 1
                if result["stocks"]:
                    stocks_str = ", ".join(
                        f"{s.get('name','?')}({s.get('code','?')}) {s.get('action','?')}"
                        for s in result["stocks"][:3])
                    print(f"✓ {len(result['stocks'])}只 → {stocks_str}", flush=True)
                else:
                    secs = "/".join(result.get("sectors", [])) or "-"
                    print(f"✓ 题材: {secs} | {result['summary'][:30]}", flush=True)
            else:
                analyzed_ids.append(dyn_id)
                print("无内容", flush=True)

    data["analyzed_ids"] = analyzed_ids
    save_picks(data)
    if new_count:
        print(f"\n✓ 新增 {new_count} 条荐股策略，总计 {len(data['picks'])} 条",
              file=sys.stderr)
    return data


# ── 策略看板 HTTP 服务 ──
def serve_dashboard(port: int = 8082) -> None:
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(PROJECT_DIR), **kwargs)

        def do_GET(self):
            from urllib.parse import urlparse
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self.path = "/templates/strategy_dashboard.html"
                super().do_GET()
            elif path == "/api/picks":
                self._json_reply(load_picks())
            elif path == "/api/quotes":
                self._handle_quotes()
            elif path == "/api/analyze":
                self._handle_analyze()
            else:
                super().do_GET()

        def do_POST(self):
            from urllib.parse import urlparse
            if urlparse(self.path).path == "/api/analyze":
                self._handle_analyze()
            else:
                self.send_response(404); self.end_headers()

        def _handle_analyze(self):
            # 后台线程重跑分析
            import threading
            def run():
                uids = resolve_uids(None)
                run_analysis(uids)
            threading.Thread(target=run, daemon=True).start()
            self._json_reply({"ok": True, "msg": "分析已启动，轮询 /api/picks 看进度"})

        def _handle_quotes(self):
            from urllib.parse import parse_qs, urlparse
            import requests as req
            qs = parse_qs(urlparse(self.path).query)
            codes_raw = "".join(qs.get("codes", [])).strip()
            if not codes_raw:
                self._json_reply({"quotes": {}}); return
            codes = [c.strip().zfill(6) for c in codes_raw.split(",") if c.strip()]
            results = {}
            for bi in range(0, len(codes), 40):
                batch = codes[bi:bi+40]
                syms = ",".join(("sh" if c.startswith(("6","5","9")) else "sz") + c for c in batch)
                try:
                    r = req.get(f"http://qt.gtimg.cn/q={syms}",
                                headers={"User-Agent": USER_AGENT,
                                         "Referer": "https://finance.qq.com/"},
                                timeout=8)
                    text = r.content.decode("gbk", errors="replace")
                except Exception:
                    continue
                for c in batch:
                    prefix = "sh" if c.startswith(("6","5","9")) else "sz"
                    key = f"v_{prefix}{c}="
                    idx = text.find(key)
                    if idx < 0: continue
                    end = text.find('";', idx)
                    if end < 0: continue
                    parts = text[idx+len(key)+1:end].split("~")
                    if len(parts) < 40: continue
                    try:
                        results[c] = {"price": float(parts[3]), "chg_pct": float(parts[32]),
                                      "name": parts[1].strip()}
                    except Exception: pass
            self._json_reply({"quotes": results})

        def _json_reply(self, data, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

        def end_headers(self):
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def log_message(self, fmt, *args):
            if args and any(k in (args[0] if args else "") for k in ("/api/",)):
                print(f"[{self.command}] {args[0]}")

    server = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"\n策略看板 → http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped."); server.server_close()


# ── CLI ──
def main():
    ap = argparse.ArgumentParser(description="B站博主荐股分析 + 策略看板")
    ap.add_argument("--serve", action="store_true", help="启动策略看板 HTTP 服务")
    ap.add_argument("--port", type=int, default=8082, help="看板端口(默认8082)")
    ap.add_argument("--reanalyze", action="store_true", help="不跳过已分析的动态(重新分析)")
    ap.add_argument("--uid", help="仅分析指定博主(逗号分隔多个, 默认读 .env BILI_UIDS)")
    args = ap.parse_args()

    if get_config() is None:
        print("⚠ 未配置 DEEPSEEK_API_KEY，只能查看已有策略，不能分析新动态。", file=sys.stderr)
        print("  请在 .env 中填入 DEEPSEEK_API_KEY=你的key", file=sys.stderr)

    uids = resolve_uids(args.uid) if args.uid else resolve_uids(None)

    if args.serve:
        serve_dashboard(args.port)
    else:
        if get_config() is None:
            print("请配置 DEEPSEEK_API_KEY 后重试，或在 .env 中填写。")
            sys.exit(1)
        data = run_analysis(uids)
        print(f"\n策略库: {PICKS_PATH}")
        print(f"已分析: {len(data['analyzed_ids'])} 条动态")
        print(f"荐股数: {len(data['picks'])} 条策略")
        if data["picks"]:
            print("\n最新荐股:")
            for p in data["picks"][:5]:
                for s in p.get("stocks", []):
                    print(f"  [{p['up_name']}] {s.get('name','?')}({s.get('code','?')}) "
                          f"{s.get('action','?')} | {s.get('timeframe','?')} | {s.get('reasoning','?')[:40]}")


if __name__ == "__main__":
    main()
