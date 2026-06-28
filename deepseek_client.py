"""DeepSeek API 轻量客户端（独立，不依赖包结构）。

从 .env 或环境变量读取 DEEPSEEK_API_KEY / BASE_URL / MODEL。
用于将 B站动态文本发给 DeepSeek 提取股票操作建议。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


def _load_env() -> None:
    """加载 .env 文件（不覆盖已有环境变量）。"""
    for p in [Path(".env"), Path(__file__).resolve().parent / ".env"]:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and os.getenv(k) is None:
                os.environ[k] = v


_load_env()


def get_config() -> Optional[Dict[str, Any]]:
    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    base_url = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
    model = (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    timeout = int((os.getenv("DEEPSEEK_TIMEOUT") or "120").strip())
    if not api_key:
        return None
    return {"api_key": api_key, "base_url": base_url.rstrip("/"), "model": model, "timeout": timeout}


def chat(messages: List[Dict[str, str]], temperature: float = 0.1,
         max_tokens: int = 2000, retries: int = 3) -> str:
    """调用 DeepSeek chat，返回 assistant 文本内容。无 key 抛异常。"""
    cfg = get_config()
    if cfg is None:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 .env 中填写")
    payload = {"model": cfg["model"], "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens,
               "response_format": {"type": "json_object"}}
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    url = f"{cfg['base_url']}/v1/chat/completions"

    last_err = None
    for i in range(retries + 1):
        try:
            r = requests.post(url, headers=headers,
                              data=json.dumps(payload, ensure_ascii=False).encode(),
                              timeout=(15, int(cfg["timeout"] * 0.85)))
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            if i < retries:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"DeepSeek 请求失败: {last_err}")


def extract_json(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """从 DeepSeek 返回文本中提取 JSON（兼容 ```json fence）。"""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        return None, raw
