"""bilibili wbi 签名实现。

wbi 是 bilibili web 端统一的接口签名机制，目的是抬高批量爬虫门槛而非防破解：
1. 从 /x/web-interface/nav 拿到 wbi_img.img_url / sub_url，取文件名得 img_key / sub_key；
2. 用固定的混淆表对 img_key+sub_key(64字符) 重排，取前 32 位得 mixin_key；
3. 请求参数加上 wts=当前秒级时间戳，按 key 字典序排序后 urlencode；
4. w_rid = md5(query + mixin_key)。

全部为 MD5 + 字符串拼接 + 写死的置换表，纯客户端逻辑，可离线复现。
mixin_key 表与流程来自 SocialSisterYi/bilibili-API-collect 的公开文档。
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from functools import reduce
from typing import Any, Dict, Tuple

import requests

# 写死的混淆表（bilibili 前端 wbi.js 中的常量）。
MIXIN_KEY_ENC_TABS = [
    46, 47, 18, 2,  53, 8,  23, 32, 15, 50, 10, 31, 58, 3,  45, 35,
    27, 43, 5,  49, 33, 9,  42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7,  16, 24, 55, 40, 61, 26, 17, 0,  1,  60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6,  63, 57, 62, 11, 36, 20, 34, 44, 52,
]

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# wbi 签名前需从值里剔除的伪字符（与前端一致）。
_PSEUDO_CHARS = re.compile(r"[!'()\*]")


def _filter_pseudo(text: str) -> str:
    return _PSEUDO_CHARS.sub("", text)


def get_mixin_key(orig: str) -> str:
    """对 img_key+sub_key(64) 按混淆表重排，取前 32 位。"""
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TABS, "")[:32]


def split_wbi_keys(img_url: str, sub_url: str) -> Tuple[str, str]:
    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    return img_key, sub_key


def sign_wbi(params: Dict[str, Any], img_key: str, sub_key: str) -> Dict[str, Any]:
    """对 params 计算 wbi 签名，返回带 wts/w_rid 的新 dict。

    注意: 必须用 quote（%20 编码空格）而非 quote_plus（+ 编码空格），
    与 B站前端的 encodeURIComponent 行为一致，否则含空格的参数会导致 -352。
    """
    mixin_key = get_mixin_key(img_key + sub_key)
    signed = dict(params)
    signed["wts"] = round(time.time())
    # 按 key 字典序排序，值剔除伪字符。
    ordered = {k: _filter_pseudo(str(v)) for k, v in sorted(signed.items())}
    query = urllib.parse.urlencode(ordered, quote_via=urllib.parse.quote)
    w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    ordered["w_rid"] = w_rid
    return ordered


class WbiSigner:
    """带缓存的 wbi 签名器：定期刷新 img_key/sub_key（官方约每月换一次）。"""

    def __init__(self, session: requests.Session, cookies: Dict[str, str],
                 headers: Dict[str, str], refresh_interval: int = 3600):
        self.session = session
        self.cookies = cookies
        self.headers = headers
        self.refresh_interval = refresh_interval  # 缓存有效期(秒)
        self._img_key: str = ""
        self._sub_key: str = ""
        self._fetched_at: float = 0.0

    def _refresh(self) -> None:
        resp = self.session.get(NAV_URL, cookies=self.cookies,
                                headers=self.headers, timeout=10)
        data = resp.json()
        # nav 在未登录时返回 code=-101，但 data.wbi_img 仍会下发；以密钥是否为准。
        wbi_img = (data.get("data") or {}).get("wbi_img") or {}
        if not wbi_img.get("img_url") or not wbi_img.get("sub_url"):
            raise RuntimeError(f"获取 wbi 密钥失败: {data.get('code')} {data.get('message')}")
        self._img_key, self._sub_key = split_wbi_keys(
            wbi_img["img_url"], wbi_img["sub_url"])
        self._fetched_at = time.time()

    def get_keys(self) -> Tuple[str, str]:
        if (not self._img_key
                or time.time() - self._fetched_at > self.refresh_interval):
            self._refresh()
        return self._img_key, self._sub_key

    def sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        img_key, sub_key = self.get_keys()
        return sign_wbi(params, img_key, sub_key)
