"""QQ音乐链接解析器。"""

from __future__ import annotations

import re
import asyncio
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import httpx
import msgspec

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    QualityInfo,
    QQMusicResponse,
)
from .decrypt import decrypt_qqmusic_file
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.downloader import Downloader
from ...utils.exceptions import ParseException, DownloadException

API_URL = "https://parse-api.sokoko.org/api/qq/song"
SOURCE_SUFFIXES = frozenset({".mflac", ".mflac0", ".mgg", ".mgg1", ".mmp4", ".flac", ".ogg", ".m4a", ".mp3"})
AUDIO_SIGNATURES = ((b"fLaC", ".flac"), (b"OggS", ".ogg"), (b"ID3", ".mp3"))


class QQMusicDecrypter:
    """下载 QMC 源文件并在发送阶段按 ekey 解密。"""

    def __init__(
        self,
        downloader: Downloader,
        url: str,
        mid: str,
        ekey: str,
        headers: dict[str, str],
    ) -> None:
        self.downloader = downloader
        self.url = url
        self.mid = mid
        self.ekey = ekey
        self.headers = headers

    async def __call__(self) -> Path:
        suffix = _source_suffix(self.url)
        source = await self.downloader.download_file(
            self.url,
            filename=f"qqmusic_{self.mid}{suffix}",
            headers=self.headers,
        )
        if not self.ekey:
            return await _ensure_audio_suffix(source)
        try:
            decrypted = await decrypt_qqmusic_file(source, self.ekey)
        except Exception as exc:
            logger.warning(f"[QQ音乐] QMC 解密失败 | mid={self.mid} | error={exc}")
            raise DownloadException("QQ音乐音频解密失败") from exc
        return decrypted


class QQMusicParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="qqmusic", display_name="QQ音乐")

    @handle(
        "c6.y.qq.com/base/fcgi-bin/u",
        r"https?://c6\.y\.qq\.com/base/fcgi-bin/u\?[^\s]*__=[A-Za-z0-9]+",
    )
    async def _parse_short_link(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        return await self.parse_with_redirect(searched.group(0))

    @handle(
        "y.qq.com",
        r"https?://y\.qq\.com/n/(?:ryqq|ryqq_v2|yqq)/song(?:Detail/|/)(?P<song_id>[A-Za-z0-9]+)",
    )
    @handle(
        "i.y.qq.com/v8/playsong.html",
        r"https?://i\.y\.qq\.com/v8/playsong\.html\?[^\s]*(?:songmid|songid)=(?P<song_id>[A-Za-z0-9]+)",
    )
    @handle(
        "i2.y.qq.com/n3/other/pages/playsong",
        r"https?://i2\.y\.qq\.com/n3/other/pages/playsong/index\.html\?[^\s]*(?:songmid|songid)=(?P<song_id>[A-Za-z0-9]+)",
    )
    async def _parse_song(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        song_id = searched.group("song_id")
        return await self._parse_song_id(song_id)

    async def _parse_song_id(self, song_id: str) -> Sequence[ContentItem]:
        client = await self.get_client()
        headers = {"Referer": "https://y.qq.com/"}
        try:
            resp = await client.get(
                API_URL,
                params={"music_id": song_id, "quality": "high"},
                headers=headers,
            )
            if resp.status_code >= 400:
                raise ParseException(f"QQ音乐接口状态码 {resp.status_code}")
            payload = msgspec.json.decode(resp.content, type=QQMusicResponse)
        except (httpx.HTTPError, msgspec.DecodeError) as exc:
            raise ParseException(f"QQ音乐接口请求失败：{exc}") from exc

        if payload.code != 200:
            raise ParseException("QQ音乐接口返回错误")

        data = payload.data
        audio_url = data.download_url
        if not audio_url.startswith(("http://", "https://")):
            raise ParseException("QQ音乐接口未返回有效音频地址")

        mid = data.id if data.id else song_id
        ekey = data.ekey
        duration = max(0.0, float(data.duration_seconds))
        title = data.title or "未知歌曲"
        artist = data.artist or "未知歌手"
        album = data.album
        cover_url = data.cover if data.cover else None
        lyric = data.lyric.strip()
        quality = data.quality.name if isinstance(data.quality, QualityInfo) else str(data.quality)

        decrypter = QQMusicDecrypter(
            self.downloader,
            audio_url,
            mid,
            ekey,
            headers,
        )

        async def load_audio() -> Path:
            return await decrypter()

        audio = self.create_audio_content(loader=load_audio, duration=duration)
        author = self.create_author(
            artist,
            data.artist_pic if data.artist_pic else None,
        )
        info_parts: list[str] = [f"时长: {_format_duration(duration)}"]
        if album:
            info_parts.append(f"专辑: {album}")
        if quality:
            info_parts.append(quality)
        extra_info = " | ".join(info_parts)

        await self.send_info(
            title=title,
            card_type="common",
            author=author,
            url=f"https://y.qq.com/n/yqq/song/{mid}",
            cover_url=cover_url,
            extra_info=extra_info,
        )

        items: list[ContentItem] = [audio]
        if lyric:
            items.append(self.create_text_content(lyric))
        return self.result(items)


def _source_suffix(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    suffix = Path(path).suffix
    return suffix if suffix in SOURCE_SUFFIXES else ".mp3"


async def _ensure_audio_suffix(path: Path) -> Path:
    try:
        header = await asyncio.to_thread(_read_header, path)
    except OSError:
        return path
    suffix = next(
        (extension for signature, extension in AUDIO_SIGNATURES if header.startswith(signature)),
        "",
    )
    if not suffix and len(header) >= 8 and header[4:8] == b"ftyp":
        suffix = ".m4a"
    if not suffix and len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0:
        suffix = ".mp3"
    if not suffix or path.suffix.lower() == suffix:
        return path
    target = path.with_suffix(suffix)
    if target.exists():
        return target
    try:
        await asyncio.to_thread(path.replace, target)
    except OSError:
        return path
    return target


def _read_header(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(12)


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"
