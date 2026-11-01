"""QQ音乐 CGI 签名与 hash33 算法。"""

from __future__ import annotations

import re
from base64 import b64encode
from hashlib import sha1

_PART_1_INDEXES = (23, 14, 6, 36, 16, 7, 19)
_PART_2_INDEXES = (16, 1, 32, 12, 19, 27, 8, 5)
_SCRAMBLE_VALUES = (89, 39, 179, 150, 218, 82, 58, 252, 177, 52, 186, 123, 120, 64, 242, 133, 143, 161, 121, 179)


def hash33(value: str, initial: int = 0) -> int:
    """计算 QQ 登录 ptqrtoken（0）或 QQ 互联 g_tk（5381）。"""
    result = initial
    for character in value:
        result = (result << 5) + result + ord(character)
    return 2147483647 & result


def zzc_sign(payload: str | bytes | bytearray) -> str:
    """计算 musics.fcg 请求体签名。"""
    payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    digest = sha1(payload_bytes).hexdigest().upper()

    part_1 = "".join(digest[index] for index in _PART_1_INDEXES)
    part_2 = "".join(digest[index] for index in _PART_2_INDEXES)
    scrambled = bytearray(20)
    for index, salt in enumerate(_SCRAMBLE_VALUES):
        scrambled[index] = salt ^ int(digest[index * 2 : index * 2 + 2], 16)
    encoded = re.sub(rb"[\\/+=]", b"", b64encode(scrambled)).decode("ascii")
    return f"zzc{part_1}{encoded}{part_2}".lower()
