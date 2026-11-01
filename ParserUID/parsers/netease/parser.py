"""网易云音乐链接解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import msgspec

from ..base import BaseParser, handle
from .media import resolve_audio
from .types import QualityLevel, normalize_level
from .detail import fetch_song
from .models import SongLyricResponse
from .request import NeteaseClient
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException
from ...utils.music_card import MusicCardInfo

MEDIA_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": "NeteaseMusic 9.0.90/5038 (iPhone; iOS 16.2; zh_CN)",
}

SONG_PATTERN = (
    r"https?://(?:[a-zA-Z0-9_-]+\.)?music\.163\.com/"
    r"(?:#/)?(?:m/)?(?:#/)?song(?:/(?P<path_id>\d+)|/?\?[^\s]*\bid=(?P<query_id>\d+))"
)


class NeteaseParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="netease", display_name="网易云音乐")

    @handle("163cn.tv", r"https?://[^\s]*163cn\.tv/[A-Za-z0-9]+")
    @handle("163.lu", r"https?://[^\s]*163\.lu/[A-Za-z0-9]+")
    async def _parse_short_link(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        return await self.parse_with_redirect(searched.group(0), headers=MEDIA_HEADERS)

    @handle("y.music.163.com", SONG_PATTERN)
    @handle("music.163.com", SONG_PATTERN)
    async def _parse_song(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        groups = searched.groupdict()
        song_id = groups["path_id"] or groups["query_id"]
        if not song_id:
            raise ParseException("无效的网易云歌曲链接")
        return await self._parse_song_id(song_id)

    async def _parse_song_id(self, song_id: str) -> Sequence[ContentItem]:
        client = await self.get_client()
        netease = NeteaseClient(client)
        song = await fetch_song(netease, song_id)
        preferred = self._quality_preference()
        audio = await resolve_audio(netease, song_id, preferred)
        lyric = await self._fetch_lyric(netease, song_id)

        async def load_audio() -> Path:
            return await self.downloader.download_file(
                audio.url,
                filename=f"netease_{song_id}_{audio.level}_{audio.br}.{audio.extension}",
                headers=MEDIA_HEADERS,
                proxy=self.proxy,
            )

        item = self.create_audio_content(
            loader=load_audio,
            duration=song.duration,
            music_card=MusicCardInfo(
                audio_url=audio.url,
                source_url=song.source_url,
                title=song.title,
                artist=song.author.name,
                cover_url=song.cover_url or "",
            ),
        )
        trial_note = f" | 试听: {audio.trial_seconds} 秒" if audio.trial_seconds > 0 else ""
        extra = f"音质: {audio.display_quality} | 格式: {audio.extension} | 时长: {song.duration_text}{trial_note}"
        if song.album:
            extra = f"{extra} | 专辑: {song.album}"
        await self.send_info(
            title=song.title,
            card_type="common",
            author=song.author,
            url=song.source_url,
            cover_url=song.cover_url,
            metrics=song.metrics,
            extra_info=extra,
        )
        contents: list[ContentItem] = [item]
        if lyric:
            contents.append(self.create_text_content(lyric))
        return self.result(contents)

    def _quality_preference(self) -> QualityLevel:
        raw = self.get_config_value("quality").strip()
        return normalize_level(raw) or "exhigh"

    async def _fetch_lyric(self, client: NeteaseClient, song_id: str) -> str:
        try:
            raw, _ = await client.eapi(
                "/api/song/lyric",
                {"id": song_id, "tv": -1, "lv": -1, "rv": -1, "kv": -1},
            )
            response = msgspec.convert(raw, type=SongLyricResponse)
        except (ParseException, msgspec.ValidationError, TypeError):
            return ""
        return response.lrc.lyric.strip()
