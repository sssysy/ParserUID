"""酷我音乐链接解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import httpx
import msgspec

from ..base import BaseParser, handle
from .models import (
    KuwoQuality,
    BodianMvResponse,
    KuwoShareResponse,
    BodianInfoResponse,
    BodianAudioResponse,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException
from ...utils.music_card import MusicCardInfo

SHARE_API = "https://parse-api.sokoko.org/api/kuwo/song"
BODIAN_INFO_API = "https://bd-api.kuwo.cn/api/service/music/info"
BODIAN_AUDIO_API = "https://bd-api.kuwo.cn/api/service/music/audioUrl"
BODIAN_MV_API = "https://bd-api.kuwo.cn/api/service/mv/info"
BODIAN_HEADERS = {
    "User-Agent": "Dart/2.18 (dart:io)",
    "plat": "ar",
    "ver": "3.1.0",
    "channel": "aliopen",
}


class KuwoParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="kuwo", display_name="酷我音乐")

    @handle(
        "kuwo.cn",
        r"https?://(?:www\.|m\.)?kuwo\.cn/.*play_detail/(?P<rid>\d+)",
    )
    async def _parse_share(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        rid = searched.group("rid")
        client = await self.get_client()
        try:
            resp = await client.get(
                SHARE_API,
                params={"music_id": rid, "quality": "320k"},
            )
            if resp.status_code >= 400:
                raise ParseException(f"酷我音乐接口状态码 {resp.status_code}")
            payload = msgspec.json.decode(resp.content, type=KuwoShareResponse)
        except (httpx.HTTPError, msgspec.DecodeError) as exc:
            raise ParseException(f"酷我音乐接口请求失败：{exc}") from exc

        if payload.code != 200:
            raise ParseException("酷我音乐接口返回错误")

        data = payload.data
        audio_url = data.download_url
        if not audio_url.startswith(("http://", "https://")):
            raise ParseException("酷我音乐未返回有效音频地址")

        title = data.title or "未知歌曲"
        artist = data.artist or "未知歌手"
        duration = max(0.0, float(data.duration_seconds))
        album = data.album
        cover_url = data.cover if data.cover else None
        avatar_url = data.artist_pic if data.artist_pic else None
        lyric = data.lyric.strip()
        quality = data.quality.name if isinstance(data.quality, KuwoQuality) else str(data.quality)

        async def load_audio() -> Path:
            return await self.downloader.download_audio(
                audio_url,
                proxy=self.proxy,
            )

        audio = self.create_audio_content(
            loader=load_audio,
            duration=duration,
            music_card=MusicCardInfo(
                audio_url=audio_url,
                source_url=f"https://www.kuwo.cn/play_detail/{rid}",
                title=title,
                artist=artist,
                cover_url=cover_url or "",
            ),
        )
        author = self.create_author(artist, avatar_url)
        extra_info = _track_info(duration, album, quality)

        await self.send_info(
            title=title,
            card_type="common",
            author=author,
            url=f"https://www.kuwo.cn/play_detail/{rid}",
            cover_url=cover_url,
            extra_info=extra_info,
        )

        items: list[ContentItem] = [audio]
        if lyric:
            items.append(self.create_lyric_content(lyric))
        return self.result(items)

    @handle(
        "h5app.kuwo.cn",
        r"https?://h5app\.kuwo\.cn/m/bodian/play(?:Music)?\.html\?"
        r"(?:[^\s]*&)?(?P<kind>musicId|mvId)=(?P<item_id>\d+)",
    )
    async def _parse_bodian(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        item_id = searched.group("item_id")
        is_mv = searched.group("kind") == "mvId"
        client = await self.get_client()

        try:
            info_resp = await client.get(
                BODIAN_INFO_API,
                params={"musicId": item_id, "uid": "-1", "token": ""},
                headers=BODIAN_HEADERS,
            )
            if info_resp.status_code >= 400:
                raise ParseException(f"波点音乐接口状态码 {info_resp.status_code}")
            info_payload = msgspec.json.decode(info_resp.content, type=BodianInfoResponse)
        except (httpx.HTTPError, msgspec.DecodeError) as exc:
            raise ParseException(f"波点音乐请求失败：{exc}") from exc

        info = info_payload.data
        title = info.name or "未知歌曲"
        artist = info.artist or "未知歌手"
        album = info.album
        cover_url = info.albumPic120 if info.albumPic120 else None
        duration = max(0.0, float(info.duration))
        info_text = _track_info(duration, album)

        if is_mv:
            try:
                mv_resp = await client.get(
                    BODIAN_MV_API,
                    params={
                        "musicId": item_id,
                        "wifi": "1",
                        "noWifi": "1",
                        "uid": "-1",
                        "token": "",
                    },
                    headers=BODIAN_HEADERS,
                )
                if mv_resp.status_code >= 400:
                    raise ParseException(f"波点 MV 接口状态码 {mv_resp.status_code}")
                mv_payload = msgspec.json.decode(mv_resp.content, type=BodianMvResponse)
            except (httpx.HTTPError, msgspec.DecodeError) as exc:
                raise ParseException(f"波点 MV 请求失败：{exc}") from exc

            mv = mv_payload.data.mv
            video_url = mv.highUrl if mv.highUrl else mv.lowUrl
            if not video_url.startswith(("http://", "https://")):
                raise ParseException("波点音乐未返回有效 MV 地址")

            cover_url = mv.coverUrl if mv.coverUrl else cover_url
            mv_duration = max(0.0, float(mv.duration)) or duration

            await self.send_info(
                title=title,
                card_type="video",
                author=self.create_author(artist),
                url=searched.group(0),
                cover_url=cover_url,
                video_duration=_format_duration(mv_duration),
                extra_info=info_text,
            )
            video = self.create_video_content(
                video_url,
                duration=mv_duration,
                headers=BODIAN_HEADERS,
                proxy=self.proxy,
            )
            return self.result([video])

        try:
            audio_resp = await client.get(
                f"{BODIAN_AUDIO_API}/{item_id}",
                params={
                    "format": "mp3",
                    "br": "320kmp3",
                    "songType": "",
                    "fromList": "",
                    "weListenUid": "",
                    "weListenDevId": "",
                },
                headers=BODIAN_HEADERS,
            )
            if audio_resp.status_code >= 400:
                raise ParseException(f"波点音频接口状态码 {audio_resp.status_code}")
            audio_payload = msgspec.json.decode(audio_resp.content, type=BodianAudioResponse)
        except (httpx.HTTPError, msgspec.DecodeError) as exc:
            raise ParseException(f"波点音频请求失败：{exc}") from exc

        audio_url = audio_payload.data.audioUrl
        if not audio_url.startswith(("http://", "https://")):
            raise ParseException("波点音乐未返回有效音频地址")

        async def load_audio() -> Path:
            return await self.downloader.download_audio(
                audio_url,
                headers=BODIAN_HEADERS,
                proxy=self.proxy,
            )

        audio = self.create_audio_content(
            loader=load_audio,
            duration=duration,
            music_card=MusicCardInfo(
                audio_url=audio_url,
                source_url=searched.group(0),
                title=title,
                artist=artist,
                cover_url=cover_url or "",
            ),
        )
        await self.send_info(
            title=title,
            card_type="common",
            author=self.create_author(artist),
            url=searched.group(0),
            cover_url=cover_url,
            extra_info=info_text,
        )
        return self.result([audio])


def _track_info(duration: float, album: str, quality: str = "") -> str:
    parts: list[str] = []
    if duration > 0:
        parts.append(f"时长: {_format_duration(duration)}")
    if album:
        parts.append(f"专辑: {album}")
    if quality:
        parts.append(quality)
    return " | ".join(parts)


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"
