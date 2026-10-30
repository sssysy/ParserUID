"""TikTok 链接解析器实现。"""

from __future__ import annotations

import re
import asyncio
from typing import ClassVar
from pathlib import Path

import msgspec

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import TikTokInfo
from ...utils.models import Platform, MediaContent
from ...utils.exceptions import ParseException


class TikTokParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="tiktok", display_name="TikTok")

    def _format_selector(self, quality: str) -> str:
        if quality == "auto":
            return "bv*+ba/b"
        height = quality.removesuffix("p")
        if not height.isdigit():
            return "bv*+ba/b"
        return f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"

    def _author_uid(self, uploader: str, url: str) -> str | None:
        clean_uploader = uploader.strip().lstrip("@")
        if clean_uploader:
            return f"@{clean_uploader}"
        matched = re.search(r"tiktok\.com/@([^/?#\s]+)/", url)
        return f"@{matched.group(1)}" if matched is not None else None

    async def _extract_info(self, url: str) -> TikTokInfo:
        import yt_dlp

        cookie = self.get_config_value("cookie")
        headers = {"Origin": "https://www.tiktok.com", "Referer": "https://www.tiktok.com/"}
        if cookie:
            headers["Cookie"] = cookie

        opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "http_headers": headers,
            "format": self._format_selector(self.get_config_value("video_quality", "auto")),
        }
        if self.proxy:
            opts["proxy"] = self.proxy

        def _sync_extract() -> TikTokInfo:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw_dict = ydl.extract_info(url, download=False)
                if not isinstance(raw_dict, dict):
                    raise ParseException("TikTok 视频元数据提取失败")
                return msgspec.convert(raw_dict, TikTokInfo)

        info = await asyncio.to_thread(_sync_extract)
        if not any(f.vcodec not in (None, "none") for f in info.formats):
            raise ParseException("TikTok 作品没有可用的视频格式")
        return info

    async def _download_video(self, url: str) -> Path:
        cookie = self.get_config_value("cookie")
        headers = {"Origin": "https://www.tiktok.com", "Referer": "https://www.tiktok.com/"}
        if cookie:
            headers["Cookie"] = cookie

        custom_opts: dict[str, object] = {
            "http_headers": headers,
            "format": self._format_selector(self.get_config_value("video_quality", "auto")),
        }
        try:
            return await self.downloader.download_with_ytdlp(
                url,
                proxy=self.proxy,
                custom_opts=custom_opts,
            )
        except Exception as exc:
            logger.warning(f"[TikTok] 视频下载失败 | error={exc}")
            raise ParseException("TikTok 视频下载失败") from exc

    @handle(
        "tiktok.com",
        r"(?:www|vt|vm)\.tiktok\.com/"
        r"(?:@[^/?#\s]+/(?:video|photo)/\d+|t/[A-Za-z0-9_-]+/?|"
        r"[A-Za-z0-9_-]+/?)",
    )
    async def _parse(self, searched: re.Match[str]) -> list[MediaContent]:
        url = f"https://{searched.group(0)}"
        info = await self._extract_info(url)

        title = info.title or info.description or "TikTok"
        channel = info.channel or info.uploader
        thumbnail = info.thumbnail or (info.thumbnails[-1].url if info.thumbnails else None)
        video_url = info.webpage_url or url

        author = self.create_author(
            name=channel or "TikTok User",
            uid=self._author_uid(info.uploader, video_url),
        )

        await self.send_info(
            title=title,
            card_type="video",
            author=author,
            cover_url=thumbnail,
            text=info.description,
            url=video_url,
            video_duration=f"{int(info.duration)}s" if info.duration > 0 else None,
        )

        content = self.create_video_content(
            None,
            lambda: self._download_video(video_url),
            duration=info.duration,
            proxy=self.proxy,
        )
        return self.result([content])
