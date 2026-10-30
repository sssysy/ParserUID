"""酷狗音乐链接解析器。"""

from __future__ import annotations

import re
import json
import base64
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import httpx
import msgspec

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    KugouPlayInfo,
    LyricSearchResponse,
    LyricDownloadResponse,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException

SONG_INFO_URL = "https://m.kugou.com/app/i/getSongInfo.php"
LYRIC_SEARCH_URL = "https://krcs.kugou.com/search"
LYRIC_DOWNLOAD_URL = "https://lyrics.kugou.com/download"
HASH_RE = re.compile(
    r"var dataFromSmarty\s*=\s*(\[.*?\]),",
    flags=re.DOTALL,
)
JSON_HASH_RE = re.compile(r'"hash"\s*:\s*"([0-9A-Fa-f]{32})"')
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


class KugouParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="kugou", display_name="酷狗音乐")

    @handle(
        "t1.kugou.com",
        r"https?://t1\.kugou\.com/(?:"
        r"(?P<share_code>[A-Za-z0-9]+)"
        r"|(?P<before_hash>[^\s]*hash=(?P<hash>[0-9A-Fa-f]{32}))"
        r")",
    )
    async def _parse_short_link(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        song_hash = searched.groupdict().get("hash")
        if song_hash:
            return await self._parse_hash(song_hash)
        return await self.parse_with_redirect(searched.group(0))

    @handle(
        "kugou.com",
        r"https?://[^\s]*?kugou\.com[^\s]*?(?:"
        r"/(?:share|mixsong)/[A-Za-z0-9]+\.html"
        r"|hash=(?P<hash>[0-9A-Fa-f]{32})"
        r"|chain=(?P<chain>[A-Za-z0-9]+)"
        r")",
    )
    async def _parse_share(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        direct_hash = searched.groupdict().get("hash")
        if direct_hash:
            return await self._parse_hash(direct_hash)
        return await self._parse_share_page(searched.group(0))

    async def _parse_share_page(self, url: str) -> Sequence[ContentItem]:
        client = await self.get_client()
        headers = {"Referer": "https://m.kugou.com/", "User-Agent": MOBILE_UA}
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                raise ParseException(f"酷狗分享页状态码 {resp.status_code}")
            html = resp.text
        except httpx.HTTPError as exc:
            raise ParseException(f"酷狗分享页请求失败：{exc}") from exc

        song_hash = _extract_hash(html)
        if not song_hash:
            raise ParseException("未找到酷狗歌曲 hash")
        return await self._parse_hash(song_hash)

    async def _parse_hash(self, song_hash: str) -> Sequence[ContentItem]:
        upper_hash = song_hash.upper()
        client = await self.get_client()
        headers = {"Referer": "https://m.kugou.com/"}
        try:
            resp = await client.get(
                SONG_INFO_URL,
                params={"cmd": "playInfo", "hash": upper_hash},
                headers=headers,
            )
            if resp.status_code >= 400:
                raise ParseException(f"酷狗接口状态码 {resp.status_code}")
            play_info = msgspec.json.decode(resp.content, type=KugouPlayInfo)
        except (httpx.HTTPError, msgspec.DecodeError) as exc:
            raise ParseException(f"酷狗歌曲请求失败：{exc}") from exc

        if play_info.errcode != 0:
            raise ParseException("酷狗歌曲不可播放")

        audio_url = play_info.url
        if not audio_url:
            raise ParseException("未找到酷狗音频地址")

        title = play_info.songName or "未知歌曲"
        artist = play_info.singerName or "未知歌手"
        duration = max(0.0, float(play_info.timeLength))
        actual_hash = play_info.hash or upper_hash
        album_audio_id = play_info.album_audio_id

        lyric = ""
        try:
            lyric = await self._fetch_lyric(actual_hash)
        except Exception as exc:
            logger.warning(f"[酷狗] 歌词获取失败 | hash={actual_hash} | error={exc}")

        cover_url = play_info.album_img.replace("{size}", "480") if play_info.album_img else None

        async def load_audio() -> Path:
            return await self.downloader.download_audio(
                audio_url,
                headers={"Referer": "https://m.kugou.com/"},
                proxy=self.proxy,
            )

        audio = self.create_audio_content(loader=load_audio, duration=duration)
        author = self.create_author(artist)

        bitrate = play_info.bitRate
        info_parts: list[str] = []
        if bitrate > 0:
            info_parts.append(f"比特率: {bitrate}K")
        info_parts.append(f"时长: {_format_duration(duration)}")
        extra_info = " | ".join(info_parts)

        song_url = f"https://www.kugou.com/song/#{actual_hash.lower()}"
        if album_audio_id:
            song_url = f"https://h5.kugou.com/v2/v-5a15aeb1/index.html?album_audio_id={album_audio_id}"

        await self.send_info(
            title=title,
            card_type="common",
            author=author,
            url=song_url,
            cover_url=cover_url,
            extra_info=extra_info,
        )

        items: list[ContentItem] = [audio]
        if lyric:
            items.append(self.create_text_content(lyric))
        return self.result(items)

    async def _fetch_lyric(self, song_hash: str) -> str:
        client = await self.get_client()
        headers = {"Referer": "https://m.kugou.com/"}
        try:
            resp = await client.get(
                LYRIC_SEARCH_URL,
                params={"hash": song_hash},
                headers=headers,
            )
            if resp.status_code >= 400:
                return ""
            search_data = msgspec.json.decode(resp.content, type=LyricSearchResponse)
        except Exception:
            return ""

        if not search_data.candidates:
            return ""

        candidate = search_data.candidates[0]
        lyric_id = candidate.id
        accesskey = candidate.accesskey
        if not lyric_id or not accesskey:
            return ""

        try:
            dl_resp = await client.get(
                LYRIC_DOWNLOAD_URL,
                params={
                    "ver": "1",
                    "id": lyric_id,
                    "accesskey": accesskey,
                    "fmt": "lrc",
                },
                headers=headers,
            )
            if dl_resp.status_code >= 400:
                return ""
            lyric_data = msgspec.json.decode(dl_resp.content, type=LyricDownloadResponse)
        except Exception:
            return ""

        if lyric_data.error_code != 0 or not lyric_data.content:
            return ""

        charset = lyric_data.charset or "utf-8"
        try:
            decoded = base64.b64decode(lyric_data.content)
            return decoded.decode(charset, errors="replace").strip()
        except (ValueError, LookupError):
            return ""


def _extract_hash(html: str) -> str:
    match = HASH_RE.search(html)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                first_dict: dict[str, object] = parsed[0]
                val = first_dict.get("hash")
                if isinstance(val, str) and re.fullmatch(r"[0-9A-Fa-f]{32}", val):
                    return val.upper()
        except (json.JSONDecodeError, TypeError):
            pass

    json_match = JSON_HASH_RE.search(html)
    return json_match.group(1).upper() if json_match else ""


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"
