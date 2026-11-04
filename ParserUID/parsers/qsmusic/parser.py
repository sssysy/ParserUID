"""汽水音乐链接解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import httpx

from ..base import BaseParser, handle
from .share import (
    cover_url,
    track_album,
    format_lyrics,
    artist_avatar_url,
    decode_share_html,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException
from ...utils.music_card import MusicCardInfo

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


class QSMusicParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name="qsmusic",
        display_name="汽水音乐",
    )

    @handle(
        "qishui.douyin.com",
        r"https?://qishui\.douyin\.com/s/[A-Za-z0-9]+/?",
    )
    async def _parse_share(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        share_url = searched.group(0)
        html = await self._fetch_share_page(share_url)
        track = decode_share_html(html)

        title = track.trackName or "未知歌曲"
        artist = track.artistName or "未知歌手"
        duration = max(0.0, float(track.duration))
        album = track_album(track)
        lyric = format_lyrics(track)
        cover = cover_url(track)
        avatar = artist_avatar_url(track)

        headers = {"User-Agent": MOBILE_UA}

        async def load_audio() -> Path:
            return await self.downloader.download_audio(
                track.url,
                headers=headers,
                proxy=self.proxy,
            )

        audio = self.create_audio_content(
            loader=load_audio,
            duration=duration,
            music_card=MusicCardInfo(
                audio_url=track.url,
                source_url=share_url,
                title=title,
                artist=artist,
                cover_url=cover or "",
            ),
        )
        author = self.create_author(
            artist,
            avatar,
        )
        info_parts: list[str] = [f"时长: {_format_duration(duration)}"]
        if album:
            info_parts.append(f"专辑: {album}")
        extra_info = " | ".join(info_parts)

        await self.send_info(
            title=title,
            card_type="common",
            author=author,
            url=share_url,
            cover_url=cover,
            extra_info=extra_info,
        )

        items: list[ContentItem] = [audio]
        if lyric:
            items.append(self.create_lyric_content(lyric))
        return self.result(items)

    async def _fetch_share_page(self, url: str) -> str:
        client = await self.get_client()
        headers = {"User-Agent": MOBILE_UA}
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                raise ParseException(f"汽水音乐分享页状态码 {resp.status_code}")
            return resp.text
        except httpx.HTTPError as exc:
            raise ParseException(f"汽水音乐分享页请求失败：{exc}") from exc


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"
