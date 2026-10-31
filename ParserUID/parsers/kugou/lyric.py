"""酷狗歌词搜索与 LRC 解码。"""

from __future__ import annotations

import base64

import httpx
import msgspec

from .models import LyricSearchResponse, LyricDownloadResponse

LYRIC_SEARCH_URL = "https://krcs.kugou.com/search"
LYRIC_DOWNLOAD_URL = "https://lyrics.kugou.com/download"
LYRIC_HEADERS = {"Referer": "https://www.kugou.com/"}


async def fetch_lyric(client: httpx.AsyncClient, song_hash: str) -> str:
    try:
        response = await client.get(
            LYRIC_SEARCH_URL,
            params={"hash": song_hash.upper()},
            headers=LYRIC_HEADERS,
        )
        response.raise_for_status()
        search = msgspec.json.decode(response.content, type=LyricSearchResponse)
    except (httpx.HTTPError, msgspec.DecodeError):
        return ""
    if not search.candidates:
        return ""
    candidate = search.candidates[0]
    if not candidate.id or not candidate.accesskey:
        return ""
    try:
        response = await client.get(
            LYRIC_DOWNLOAD_URL,
            params={
                "ver": "1",
                "id": candidate.id,
                "accesskey": candidate.accesskey,
                "fmt": "lrc",
            },
            headers=LYRIC_HEADERS,
        )
        response.raise_for_status()
        lyric = msgspec.json.decode(response.content, type=LyricDownloadResponse)
    except (httpx.HTTPError, msgspec.DecodeError):
        return ""
    if lyric.error_code != 0 or not lyric.content:
        return ""
    try:
        decoded = base64.b64decode(lyric.content)
    except ValueError:
        return ""
    return decoded.decode(lyric.charset or "utf-8", errors="replace").strip()
