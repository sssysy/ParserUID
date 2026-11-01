"""QQ音乐歌词获取与明文字幕解码。"""

from __future__ import annotations

import base64
import binascii

from gsuid_core.logger import logger

from .client import QqMusicClient
from .models import QqTrack, QqCredential
from ...utils.exceptions import ParseException


async def fetch_lyric(
    client: QqMusicClient,
    track: QqTrack,
    credential: QqCredential | None,
) -> str:
    try:
        payload = await client.lyric(track.id, track.mid, credential)
    except ParseException as exc:
        logger.info(f"[Parser·QQ音乐] 歌词获取失败 | error_type={type(exc).__name__}")
        return ""
    return _decode_plain_lyric(payload.lyric, payload.crypt).strip()


def _decode_plain_lyric(raw: str, crypt: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if crypt != 0 or value.startswith("["):
        return value
    try:
        decoded = base64.b64decode(value).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return value
    if "[" not in decoded and "\n" not in decoded:
        return value
    return decoded
