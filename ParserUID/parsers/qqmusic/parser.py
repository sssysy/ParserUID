"""QQ音乐原生 CGI 链接解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import httpx

from gsuid_core.logger import logger

from .qmc import QmcError
from .auth import load_credential, credential_from_config, refresh_saved_credential
from ..base import BaseParser, handle
from .lyric import fetch_lyric
from .client import WEB_UA, QqAuthError, QqMusicClient
from .models import QqTrack, QqSinger, QqCredential, QqInfoSection, QqSongDetailData
from .decrypt import decrypt_qqmusic_file
from .quality import ResolvedAudio, resolve_audio, normalize_quality
from ...sender import ContentItem
from ...utils.models import Platform
from ...render.models import Metric
from ...utils.exceptions import ParseException, DownloadException
from ...utils.music_card import MusicCardInfo

_MEDIA_HEADERS = {"Referer": "https://y.qq.com/", "User-Agent": WEB_UA}
_ENCRYPTED_SUFFIXES = frozenset({".mflac", ".mflac0", ".mgg", ".mgg1", ".mmp4"})


class QQMusicParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="qqmusic", display_name="QQ音乐")

    @handle(
        "c6.y.qq.com",
        r"https?://c6\.y\.qq\.com/base/fcgi-bin/u\?[^\s]*__=[A-Za-z0-9]+",
    )
    @handle(
        "c.y.qq.com",
        r"https?://c\.y\.qq\.com/base/fcgi-bin/u\?[^\s]*__=[A-Za-z0-9]+",
    )
    async def _parse_short_link(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        return await self.parse_with_redirect(searched.group(0), headers=_MEDIA_HEADERS)

    @handle(
        "y.qq.com",
        r"https?://y\.qq\.com/n/(?:ryqq|ryqq_v2|yqq)/song(?:Detail/|/)(?P<song_id>[A-Za-z0-9]+)",
    )
    @handle(
        "i.y.qq.com",
        r"https?://i\.y\.qq\.com/v8/playsong\.html\?[^\s#]*(?:songmid|songid)=(?P<song_id>[A-Za-z0-9]+)",
    )
    @handle(
        "i2.y.qq.com",
        r"https?://i2\.y\.qq\.com/n3/other/pages/playsong/index\.html\?[^\s#]*"
        r"(?:songmid|songid)=(?P<song_id>[A-Za-z0-9]+)",
    )
    async def _parse_song(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        return await self._parse_song_id(searched.group("song_id"))

    async def _parse_song_id(self, song_id: str) -> Sequence[ContentItem]:
        client = await self.get_client()
        credential = await self._credential(client)
        qqmusic = QqMusicClient(client)
        detail = await qqmusic.song_detail(song_id, credential)
        track = detail.track_info
        if not track.mid:
            raise ParseException("QQ音乐未返回歌曲 MID")

        preferred = normalize_quality(self.get_config_value("quality", "auto"))
        try:
            audio = await resolve_audio(qqmusic, track, credential, preferred)
        except QqAuthError:
            if credential is None or self._manual_cookie_active():
                raise
            refreshed = await refresh_saved_credential(client, credential)
            if refreshed is None:
                raise
            credential = refreshed
            audio = await resolve_audio(qqmusic, track, credential, preferred)
        lyric = await fetch_lyric(qqmusic, track, credential)
        await self._send_song_card(detail, track, audio)

        async def load_audio() -> Path:
            return await self._download_audio(track, audio)

        contents: list[ContentItem] = [
            self.create_audio_content(
                loader=load_audio,
                duration=max(0.0, float(track.interval)),
                music_card=MusicCardInfo(
                    audio_url=audio.url,
                    source_url=f"https://y.qq.com/n/ryqq/songDetail/{track.mid}",
                    title=track.title or track.name or "未知歌曲",
                    artist=" / ".join(singer.name for singer in track.singer if singer.name) or "未知歌手",
                    cover_url=_album_cover(track) or "",
                ),
            )
        ]
        if lyric:
            contents.append(self.create_text_content(lyric))
        return self.result(contents)

    async def _credential(self, client: httpx.AsyncClient) -> QqCredential | None:
        configured = self.get_config_value("cookie").strip()
        if configured:
            credential = credential_from_config(configured)
            if credential is None:
                raise ParseException("QQ音乐 Cookie 配置无效")
            return credential
        credential = await load_credential()
        if credential is not None and credential.needs_refresh():
            refreshed = await refresh_saved_credential(client, credential)
            if refreshed is not None:
                return refreshed
        return credential

    def _manual_cookie_active(self) -> bool:
        return bool(self.get_config_value("cookie").strip())

    async def _send_song_card(
        self,
        detail: QqSongDetailData,
        track: QqTrack,
        audio: ResolvedAudio,
    ) -> None:
        title = track.title or track.name or "未知歌曲"
        author_name = " / ".join(singer.name for singer in track.singer if singer.name) or "未知歌手"
        avatar_url = _singer_avatar(track.singer)
        cover_url = _album_cover(track)
        album = track.album.title or track.album.name
        genre = _section_value(detail.info.genre)
        language = _section_value(detail.info.lan)
        company = _section_value(detail.info.company)
        release_date = _section_value(detail.info.pub_time) or track.time_public
        intro = _section_value(detail.info.intro) or track.subtitle

        extra_parts = [f"音质: {audio.quality_label}", f"格式: {audio.extension.lstrip('.').upper()}"]
        if album:
            extra_parts.append(f"专辑: {album}")
        if genre:
            extra_parts.append(f"流派: {genre}")
        if language:
            extra_parts.append(f"语言: {language}")
        if company:
            extra_parts.append(f"发行: {company}")
        metrics = (
            Metric("音质", audio.quality_label),
            Metric("时长", _format_duration(track.interval)),
            Metric("格式", audio.extension.lstrip(".").upper()),
        )
        if release_date:
            metrics = (*metrics, Metric("发行", release_date))

        await self.send_info(
            title=title,
            card_type="common",
            author=self.create_author(author_name, avatar_url),
            text=_short_text(intro),
            url=f"https://y.qq.com/n/ryqq/songDetail/{track.mid}",
            cover_url=cover_url,
            cover_headers=_MEDIA_HEADERS,
            metrics=metrics,
            extra_info=" | ".join(extra_parts),
        )

    async def _download_audio(self, track: QqTrack, audio: ResolvedAudio) -> Path:
        source_name = f"qqmusic_{track.mid}_{audio.quality.key}{audio.extension}"
        source = await self.downloader.download_file(
            audio.url,
            filename=source_name,
            headers=_MEDIA_HEADERS,
            proxy=self.proxy,
            fallback_urls=audio.fallback_urls,
        )
        if not audio.ekey and audio.extension not in _ENCRYPTED_SUFFIXES:
            return source
        if not audio.ekey:
            raise DownloadException("QQ音乐加密音频缺少解密 ekey")
        try:
            return await decrypt_qqmusic_file(source, audio.ekey)
        except (OSError, ValueError, QmcError, DownloadException) as exc:
            logger.warning(
                f"[Parser·QQ音乐] QMC 解密失败 | mid={track.mid} | quality={audio.quality.key} "
                f"| error_type={type(exc).__name__}"
            )
            raise DownloadException("QQ音乐音频解密失败") from exc


def _album_cover(track: QqTrack) -> str | None:
    mid = track.album.mid or track.album.pmid
    return _photo_url("T002", mid, 800) if mid else None


def _singer_avatar(singers: Sequence[QqSinger]) -> str | None:
    for singer in singers:
        mid = singer.mid or singer.pmid
        if mid:
            return _photo_url("T001", mid, 300)
    return None


def _photo_url(kind: str, mid: str, size: int) -> str:
    return f"https://y.gtimg.cn/music/photo_new/{kind}R{size}x{size}M000{mid}.jpg"


def _section_value(section: QqInfoSection) -> str:
    for item in section.content:
        if item.value:
            return item.value.strip()
    return ""


def _short_text(value: str, limit: int = 600) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _format_duration(seconds: int | float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"
