"""网易云音乐链接解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import httpx
import msgspec

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    SongUrlItem,
    SongUrlResponse,
    SongLyricResponse,
    SongDetailResponse,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException

API_BASE = "https://music.163.com/api"
SONG_DETAIL_PATH = "/song/detail/"
SONG_LYRIC_PATH = "/song/lyric"
SONG_URL_PATH = "/song/enhance/player/url"
MEDIA_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}
AUDIO_EXTENSIONS = frozenset({"flac", "wav", "m4a", "aac", "mp3"})
QUALITY_CHOICES = ((999000, "lossless"), (320000, "exhigh"), (192000, "higher"), (128000, "standard"))


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"


class NeteaseParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name="netease",
        display_name="网易云音乐",
    )

    async def _get_api(
        self,
        endpoint: str,
        params: dict[str, str | int | float],
    ) -> bytes:
        client = await self.get_client()
        try:
            resp = await client.get(
                f"{API_BASE}{endpoint}",
                params=params,
                headers=MEDIA_HEADERS,
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
        r"|(?:#/)?song\?[^\s]*\bid=(?P<query_id>\d+)"
        r")",
    )
    async def _parse_song(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        groups = searched.groupdict()
        song_id = groups.get("path_id") or groups.get("query_id")
        if not song_id:
            raise ParseException("无效的网易云歌曲链接")
        return await self._parse_song_id(song_id)

    async def _parse_song_id(self, song_id: str) -> Sequence[ContentItem]:
        info_raw = await self._get_api(
            SONG_DETAIL_PATH,
            {"id": song_id, "ids": f"[{song_id}]"},
        )
        info_resp = msgspec.json.decode(info_raw, type=SongDetailResponse)
        if info_resp.code != 200 or not info_resp.songs:
            raise ParseException("网易云歌曲信息接口返回错误")

        song = info_resp.songs[0]
        title = song.name.strip() or "未知歌曲"
        artist = " / ".join(value.name for value in song.artists if value.name.strip()) or "未知歌手"
        duration = max(0.0, song.duration / 1000)

        lyric = ""
        try:
            lyric_raw = await self._get_api(
                SONG_LYRIC_PATH,
                {"id": song_id, "lv": 1, "kv": 1, "tv": -1},
            )
            lyric_resp = msgspec.json.decode(lyric_raw, type=SongLyricResponse)
            lyric = lyric_resp.lrc.lyric.strip()
        except Exception as exc:
            logger.warning(f"[网易云] 歌词获取失败 | song_id={song_id} | error={exc}")

        audio_url = ""
        level = "standard"
        audio_item: SongUrlItem | None = None
        for bitrate, candidate_level in QUALITY_CHOICES:
            try:
                url_raw = await self._get_api(
                    SONG_URL_PATH,
                    {"id": song_id, "ids": f"[{song_id}]", "br": bitrate},
                )
                url_resp = msgspec.json.decode(url_raw, type=SongUrlResponse)
                candidate = url_resp.data[0] if url_resp.data else None
                if candidate is not None and candidate.url:
                    audio_url = candidate.url
                    level = candidate.level or candidate_level
                    audio_item = candidate
                    break
            except Exception as exc:
                logger.warning(f"[网易云] 音源获取失败 | level={candidate_level} | error={exc}")

        if not audio_url or audio_item is None:
            raise ParseException("无法获取网易云音频地址")

        cover_url = song.album.picUrl or None
        target_audio_url = audio_url
        extension = audio_item.type.lower() if audio_item.type.lower() in AUDIO_EXTENSIONS else "mp3"

        async def load_audio() -> Path:
            filename = f"netease_{song_id}.{extension}"
            return await self.downloader.download_file(
                target_audio_url,
                filename=filename,
                headers=MEDIA_HEADERS,
                proxy=self.proxy,
            )

        audio = self.create_audio_content(loader=load_audio, duration=duration)
        author = self.create_author(artist)
        extra_info = (
            f"音质: {level} | 码率: {audio_item.br // 1000}kbps | "
            f"格式: {extension} | 时长: {_format_duration(duration)}"
        )

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
