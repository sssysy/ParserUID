"""YouTube 链接解析器实现。"""

from __future__ import annotations

import re
import asyncio
from typing import Final, ClassVar
from pathlib import Path

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .media import (
    parse_target,
    media_is_audio,
    download_ranges,
    audio_format_selector,
    video_format_selector,
)
from .cookie import YouTubeCookieFile
from .models import VideoData, decode_video
from ...utils.models import Platform, MediaContent
from ...render.models import Author
from ...utils.exceptions import ParseException, RegionRestrictedException

_REGION_RESTRICTED_MARKERS: Final = (
    "not available in your country",
    "blocked it in your country",
    "geo restricted",
    "blocked due to the claimed content",
)
_REGION_RESTRICTED_MESSAGE: Final = "该视频受版权或地区限制，当前网络地区无法观看"


class YouTubeParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="youtube", display_name="YouTube")

    def _get_headers(self) -> dict[str, str]:
        headers = {"Referer": "https://www.youtube.com/"}
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @property
    def _cookie_source(self) -> str:
        return self.get_config_value("cookie")

    @handle(
        "youtube.com/watch",
        r"(?<![A-Za-z0-9.-])(?:www\.|m\.)?youtube\.com/watch\?"
        r"[^\s<>'\"]*(?<![A-Za-z0-9_-])v=(?P<vid>[A-Za-z0-9_-]{11})"
        r"(?![A-Za-z0-9_-])",
    )
    @handle(
        "youtube.com/shorts/",
        r"(?<![A-Za-z0-9.-])(?:www\.|m\.)?youtube\.com/shorts/"
        r"(?P<vid>[A-Za-z0-9_-]{11})",
    )
    @handle(
        "youtube.com/live/",
        r"(?<![A-Za-z0-9.-])(?:www\.|m\.)?youtube\.com/live/"
        r"(?P<vid>[A-Za-z0-9_-]{11})",
    )
    @handle(
        "youtube.com/embed/",
        r"(?<![A-Za-z0-9.-])(?:www\.|m\.)?youtube\.com/embed/"
        r"(?P<vid>[A-Za-z0-9_-]{11})",
    )
    @handle(
        "music.youtube.com/",
        r"(?<![A-Za-z0-9.-])music\.youtube\.com/watch\?"
        r"[^\s<>'\"]*(?<![A-Za-z0-9_-])v=(?P<vid>[A-Za-z0-9_-]{11})"
        r"(?![A-Za-z0-9_-])",
    )
    @handle(
        "youtu.be",
        r"(?<![A-Za-z0-9.-])youtu\.be/(?P<vid>[A-Za-z0-9_-]{11})",
    )
    async def _parse(self, searched: re.Match[str]) -> list[MediaContent]:
        matched = searched.group(0)
        try:
            target = parse_target(matched)
        except ValueError as exc:
            raise ParseException(str(exc)) from exc

        video = await self._extract(target.url)
        author = self.create_author(
            name=video.channel or video.uploader or "YouTube",
            uid=video.channel_id or None,
        )

        include_channel = self.get_config_value("include_channel_info", "true").lower() == "true"
        if include_channel and video.channel_id:
            author = await self._enrich_author(author, video)

        media_mode = self.get_config_value("media_mode", "auto")
        is_audio = media_is_audio(target, media_mode)
        clip = self._clip_seconds(video.duration)

        video_quality = self.get_config_value("video_quality", "720p")
        await self.send_info(
            title=video.title,
            card_type="video",
            author=author,
            cover_url=video.thumbnail,
            text=video.description or "",
            url=target.url,
            timestamp=video.timestamp,
            video_duration=f"{int(video.duration)}s" if video.duration > 0 else None,
            video_quality=None if is_audio else video_quality,
        )

        if is_audio:
            content: MediaContent = self.create_audio_content(
                None,
                lambda: self._download_audio(target.url),
                duration=video.duration,
            )
        else:
            content = self.create_video_content(
                None,
                lambda: self._download_video(target.url, clip),
                duration=video.duration,
                proxy=self.proxy,
            )
        return self.result([content])

    def _clip_seconds(self, duration: float) -> int | None:
        raw = self.get_config_value("clip_seconds", "0")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value > 0 and duration > value:
            return value
        return None

    async def _extract(self, url: str) -> VideoData:
        import yt_dlp

        opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "http_headers": self._get_headers(),
        }
        if self.proxy:
            opts["proxy"] = self.proxy
        cookie_file = await self._cookiefile()
        if cookie_file is not None:
            opts["cookiefile"] = str(cookie_file)

        def _sync_extract() -> VideoData:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw_dict = ydl.extract_info(url, download=False)
                if not isinstance(raw_dict, dict):
                    raise ParseException("YouTube 视频信息提取失败")
                return decode_video(raw_dict)

        try:
            return await asyncio.to_thread(_sync_extract)
        except Exception as exc:
            logger.warning(f"[YouTube] 元数据获取失败 | error={exc}")
            if _is_region_restricted(exc):
                raise RegionRestrictedException(_REGION_RESTRICTED_MESSAGE) from exc
            raise ParseException(f"YouTube 视频信息获取失败：{exc}") from exc

    async def _download_video(self, url: str, clip: int | None) -> Path:
        quality = self.get_config_value("video_quality", "720p")
        codec = self.get_config_value("video_codec", "auto")
        selector = video_format_selector(quality, codec)

        custom_opts: dict[str, object] = {
            "format": selector,
            "http_headers": self._get_headers(),
            "concurrent_fragment_downloads": 4,
        }
        cookie_file = await self._cookiefile()

        try:
            return await self.downloader.download_with_ytdlp(
                url,
                proxy=self.proxy,
                custom_opts=custom_opts,
                cookiefile=cookie_file,
                download_ranges=download_ranges(clip),
            )
        except Exception as exc:
            logger.warning(f"[YouTube] 视频下载失败 | error={exc}")
            if _is_region_restricted(exc):
                raise RegionRestrictedException(_REGION_RESTRICTED_MESSAGE) from exc
            raise ParseException(f"YouTube 视频下载失败：{exc}") from exc

    async def _download_audio(self, url: str) -> Path:
        custom_opts: dict[str, object] = {
            "format": audio_format_selector(),
            "http_headers": self._get_headers(),
        }
        cookie_file = await self._cookiefile()
        try:
            return await self.downloader.download_with_ytdlp(
                url,
                proxy=self.proxy,
                custom_opts=custom_opts,
                cookiefile=cookie_file,
                audio_only=True,
            )
        except Exception as exc:
            logger.warning(f"[YouTube] 音频下载失败 | error={exc}")
            if _is_region_restricted(exc):
                raise RegionRestrictedException(_REGION_RESTRICTED_MESSAGE) from exc
            raise ParseException(f"YouTube 音频下载失败：{exc}") from exc

    async def _cookiefile(self) -> Path | None:
        if not self._cookie_source:
            return None
        return await YouTubeCookieFile(self._cookie_source).prepare()

    async def _enrich_author(self, author: Author, video: VideoData) -> Author:
        try:
            payload = await self._fetch_channel_page(video.channel_id)
            avatar, description = self._parse_channel_page(payload)
            if avatar or description:
                return self.create_author(
                    name=video.channel or video.uploader or "YouTube",
                    avatar_url=avatar,
                    description=description,
                    uid=video.channel_id or None,
                )
        except Exception as exc:
            logger.debug(f"[YouTube] 频道资料补全失败，使用基础信息 | error={exc}")
        return author

    async def _fetch_channel_page(self, channel_id: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20251002.00.00",
                    "hl": "zh-CN",
                    "gl": "US",
                }
            },
            "browseId": channel_id,
        }
        client = await self.get_client()
        resp = await client.post(
            "https://www.youtube.com/youtubei/v1/browse",
            params={"prettyPrint": "false"},
            json=payload,
            headers=self._get_headers(),
        )
        if resp.status_code >= 400:
            return {}
        result = resp.json()
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _parse_channel_page(payload: dict[str, object]) -> tuple[str | None, str | None]:
        metadata_obj = payload.get("metadata")
        metadata = metadata_obj if isinstance(metadata_obj, dict) else {}
        channel_obj = metadata.get("channelMetadataRenderer")
        channel = channel_obj if isinstance(channel_obj, dict) else {}
        avatar_obj = channel.get("avatar")
        avatar_dict = avatar_obj if isinstance(avatar_obj, dict) else {}
        thumbnails = avatar_dict.get("thumbnails")
        avatar_urls: list[str] = []
        if isinstance(thumbnails, list):
            for item in thumbnails:
                if isinstance(item, dict):
                    url_val = item.get("url")
                    if isinstance(url_val, str) and url_val:
                        avatar_urls.append(url_val)
        desc_val = channel.get("description")
        desc = str(desc_val) if isinstance(desc_val, str) else None
        return (avatar_urls[-1] if avatar_urls else None, desc)


def _is_region_restricted(exc: Exception) -> bool:
    """按 yt-dlp 的受限提示识别地区或版权拦截。"""
    message = str(exc).lower()
    return any(marker in message for marker in _REGION_RESTRICTED_MARKERS)
