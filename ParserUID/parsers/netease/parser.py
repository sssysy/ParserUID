"""网易云音乐链接解析器。"""

from __future__ import annotations

import re
import time
import random
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import httpx
import msgspec

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    SongUrlResponse,
    SongInfoResponse,
    SongLyricResponse,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException

API_BASE = "https://nextmusic.toubiec.cn/api"
API_REFERER = "https://wyapi.toubiec.cn/"
MEDIA_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}
AUDIO_EXTENSIONS = frozenset({"flac", "wav", "m4a", "aac", "mp3"})


def random_ip() -> str:
    return ".".join(str(random.randint(0, 255)) for _ in range(4))


def parse_duration_to_seconds(duration: str) -> int:
    parts = duration.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("非法时长")
    values = [int(part) for part in parts]

    if len(values) == 1:
        hours, minutes, seconds = 0, 0, values[0]
    elif len(values) == 2:
        hours, minutes, seconds = 0, values[0], values[1]
    else:
        hours, minutes, seconds = values
    if not (hours >= 0 and 0 <= minutes < 60 and 0 <= seconds < 60):
        raise ValueError("非法时长")
    return hours * 3600 + minutes * 60 + seconds


def audio_extension(url: str) -> str:
    path = url.split("?", 1)[0]
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return extension if extension in AUDIO_EXTENSIONS else "mp3"


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"


class NeteaseParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name="netease",
        display_name="网易云音乐",
    )

    async def _post_api(self, endpoint: str, payload: dict[str, str | int]) -> bytes:
        client = await self.get_client()
        request_payload: dict[str, str | int] = {
            **payload,
            "timestamp": int(time.time() * 1000),
            "ip": random_ip(),
        }
        headers = {**MEDIA_HEADERS, "Referer": API_REFERER}
        try:
            resp = await client.post(
                f"{API_BASE}/{endpoint}",
                json=request_payload,
                headers=headers,
            )
            if resp.status_code >= 400:
                raise ParseException(f"网易云接口状态码 {resp.status_code}")
            return resp.content
        except httpx.HTTPError as exc:
            raise ParseException(f"网易云接口请求失败：{exc}") from exc

    @handle("163cn.tv", r"https?://[^\s]*163cn\.tv/[A-Za-z0-9]+")
    async def _parse_short_link(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        return await self.parse_with_redirect(searched.group(0))

    @handle(
        "music.163.com",
        r"https?://music\.163\.com/(?:"
        r"song/(?P<path_id>\d+)"
        r"|(?:#/)?song\?[^\s]*id=(?P<query_id>\d+)"
        r")",
    )
    async def _parse_song(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        groups = searched.groupdict()
        song_id = groups.get("path_id") or groups.get("query_id")
        if not song_id:
            raise ParseException("无效的网易云歌曲链接")
        return await self._parse_song_id(song_id)

    async def _parse_song_id(self, song_id: str) -> Sequence[ContentItem]:
        info_raw = await self._post_api("getSongInfo", {"id": song_id})
        info_resp = msgspec.json.decode(info_raw, type=SongInfoResponse)
        if info_resp.code != 200:
            raise ParseException("网易云歌曲信息接口返回错误")

        song = info_resp.data
        title = song.name or "未知歌曲"
        artist = song.singer or "未知歌手"
        try:
            duration = float(parse_duration_to_seconds(song.duration))
        except ValueError:
            duration = 0.0

        lyric = ""
        try:
            lyric_raw = await self._post_api("getSongLyric", {"id": song_id})
            lyric_resp = msgspec.json.decode(lyric_raw, type=SongLyricResponse)
            lyric = lyric_resp.data.lrc.strip()
        except Exception as exc:
            logger.warning(f"[网易云] 歌词获取失败 | song_id={song_id} | error={exc}")

        audio_url = ""
        level = "standard"
        for candidate in ("lossless", "standard"):
            try:
                url_raw = await self._post_api("getSongUrl", {"id": song_id, "level": candidate})
                url_resp = msgspec.json.decode(url_raw, type=SongUrlResponse)
                if url_resp.code == 200 and url_resp.data.url:
                    audio_url = url_resp.data.url
                    level = candidate
                    break
            except Exception as exc:
                logger.warning(f"[网易云] 音源获取失败 | level={candidate} | error={exc}")

        if not audio_url:
            raise ParseException("无法获取网易云音频地址")

        cover_url = song.picimg if song.picimg else None
        target_audio_url = audio_url

        async def load_audio() -> Path:
            ext = audio_extension(target_audio_url)
            filename = f"netease_{song_id}.{ext}"
            return await self.downloader.download_file(
                target_audio_url,
                filename=filename,
                headers=MEDIA_HEADERS,
                proxy=self.proxy,
            )

        audio = self.create_audio_content(loader=load_audio, duration=duration)
        author = self.create_author(artist)
        extra_info = f"音质: {level} | 格式: {audio_extension(audio_url)} | 时长: {_format_duration(duration)}"

        await self.send_info(
            title=title,
            card_type="common",
            author=author,
            url=f"https://music.163.com/song/{song_id}",
            cover_url=cover_url,
            extra_info=extra_info,
        )

        items: list[ContentItem] = [audio]
        if lyric:
            items.append(self.create_text_content(lyric))
        return self.result(items)
