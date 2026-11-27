"""哔哩哔哩 WBI 签名与 nav 密钥解析。"""

from __future__ import annotations

import time
from typing import Final
from hashlib import md5
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import quote, urlsplit
from collections.abc import Mapping

MIXIN_KEY_ENC_TAB: Final[tuple[int, ...]] = (
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
)
_FILTER_CHARS: Final = frozenset("!'()*")


@dataclass(frozen=True, slots=True)
class WbiKeys:
    """缓存的 B 站 WBI 密钥对与混合密钥。"""

    img_key: str
    sub_key: str
    mixin_key: str
    fetched_at: float


def extract_wbi_key_from_url(url: str) -> str:
    """从 nav 接口返回的 wbi_img URL 中提取密钥名。"""
    path = urlsplit(url.strip()).path
    if not path:
        return ""
    return Path(path).stem


def generate_mixin_key(img_key: str, sub_key: str) -> str:
    """按 MIXIN_KEY_ENC_TAB 重排 img_key 与 sub_key 并截取前 32 位。"""
    raw_key = f"{img_key}{sub_key}"
    if len(raw_key) <= max(MIXIN_KEY_ENC_TAB):
        raise ValueError("WBI 原始密钥长度不足")
    return "".join(raw_key[idx] for idx in MIXIN_KEY_ENC_TAB)[:32]


def parse_nav_wbi_keys(
    payload: Mapping[str, object],
    *,
    fetched_at: float,
) -> WbiKeys | None:
    """从 nav 接口响应中提取 WBI 密钥；未登录状态(-101)同样包含 wbi_img。"""
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    wbi_img = data.get("wbi_img")
    if not isinstance(wbi_img, Mapping):
        return None
    img_url = wbi_img.get("img_url")
    sub_url = wbi_img.get("sub_url")
    if not isinstance(img_url, str) or not isinstance(sub_url, str):
        return None
    img_key = extract_wbi_key_from_url(img_url)
    sub_key = extract_wbi_key_from_url(sub_url)
    if not img_key or not sub_key:
        return None
    try:
        mixin_key = generate_mixin_key(img_key, sub_key)
    except ValueError:
        return None
    return WbiKeys(
        img_key=img_key,
        sub_key=sub_key,
        mixin_key=mixin_key,
        fetched_at=fetched_at,
    )


def sign_wbi_params(
    params: Mapping[str, str | int],
    *,
    img_key: str,
    sub_key: str,
    wts: int | None = None,
) -> dict[str, str]:
    """为请求参数补充 wts 与 w_rid 签名。"""
    mixin_key = generate_mixin_key(img_key, sub_key)
    raw_params: dict[str, str | int] = dict(params)
    raw_params["wts"] = round(time.time()) if wts is None else wts

    cleaned: dict[str, str] = {}
    for key, value in sorted(raw_params.items(), key=lambda item: item[0]):
        cleaned[key] = "".join(ch for ch in str(value) if ch not in _FILTER_CHARS)

    # 按 encodeURIComponent 约定编码（空格编码为 %20，十六进制大写）。
    encoded_query = "&".join(f"{quote(key, safe='')}={quote(val, safe='')}" for key, val in cleaned.items())
    w_rid = md5(f"{encoded_query}{mixin_key}".encode("utf-8")).hexdigest()
    cleaned["w_rid"] = w_rid
    return cleaned
