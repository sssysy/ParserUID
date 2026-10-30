"""Instagram 帖子、Reels 与视频链接解析器。"""

from __future__ import annotations

import re
import asyncio
from typing import ClassVar
from collections.abc import Sequence

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .media import (
    InstagramPost,
    InstagramMedia,
    InstagramTarget,
    image_urls,
    parse_target,
    video_formats,
    pick_thumbnail,
    unwrap_entries,
    format_selector,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException


class InstagramParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name="instagram",
        display_name="Instagram",
    )

    @property
    def ig_headers(self) -> dict[str, str]:
        headers = {
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/",
        }
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @handle(
        "instagram.com/p/",
        r"(?<![A-Za-z0-9.-])https?://(?:www\.)?instagram\.com/p/(?P<code>[A-Za-z0-9_-]+)",
    )
    @handle(
        "instagram.com/reel",
        r"(?<![A-Za-z0-9.-])https?://(?:www\.)?instagram\.com/reels?/(?P<code>[A-Za-z0-9_-]+)",
    )
    @handle(
        "instagram.com/tv/",
        r"(?<![A-Za-z0-9.-])https?://(?:www\.)?instagram\.com/tv/(?P<code>[A-Za-z0-9_-]+)",
    )
    @handle(
        "instagram.com/share/",
        r"(?<![A-Za-z0-9.-])https?://(?:www\.)?instagram\.com/share/(?:(?:p|reels?|tv)/)?(?P<code>[A-Za-z0-9_-]+)",
    )
    @handle(
        "instagr.am/",
        r"(?<![A-Za-z0-9.-])https?://(?:www\.)?instagr\.am/(?:p|reels?|tv)/(?P<code>[A-Za-z0-9_-]+)",
    )
    async def _parse(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        try:
            target = parse_target(searched.group(0))
        except ValueError as exc:
            raise ParseException(str(exc)) from exc

        if target.kind == "share":
            target = await self._resolve_share(target)

        info = await self._extract(target)
        post = self._build_post(target, info)
        await self._send_description(post)

        contents = self._build_contents(post)
        if not contents:
            raise ParseException("Instagram 作品没有可发送的媒体")
        return self.result(contents)

    async def _resolve_share(self, target: InstagramTarget) -> InstagramTarget:
        final_url = await self.get_final_url(target.url, headers=self.ig_headers)
        try:
            resolved = parse_target(final_url)
        except ValueError as exc:
            raise ParseException("Instagram 分享链接无法解析") from exc
        if resolved.kind == "share":
            raise ParseException("Instagram 分享链接未跳转到作品页")
        return resolved

    async def _extract(self, target: InstagramTarget) -> dict[str, object]:
        try:
            import yt_dlp
        except ImportError as exc:
            raise ParseException("需要安装 yt-dlp 才能解析 Instagram 内容") from exc

        opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }
        if self.proxy:
            opts["proxy"] = self.proxy
        cookie = self.get_config_value("cookie")
        if cookie:
            opts["http_headers"] = {"Cookie": cookie}

        def _sync_extract() -> dict[str, object]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(target.url, download=False)
                return {str(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}

        try:
            return await asyncio.to_thread(_sync_extract)
        except Exception as exc:
            logger.warning(f"[instagram] 元数据获取失败 | error={exc} | shortcode={target.shortcode}")
            raise ParseException("Instagram 作品信息获取失败") from exc

    @staticmethod
    def _build_post(target: InstagramTarget, info: dict[str, object]) -> InstagramPost:
        entries = unwrap_entries(info)
        if not entries:
            raise ParseException("Instagram 作品条目列表为空")

        medias: list[InstagramMedia] = []
        for entry in entries:
            formats = video_formats(entry)
            if formats:
                selected = max(
                    formats,
                    key=lambda item: _int_or_zero(item.get("height")),
                )
                medias.append(
                    InstagramMedia(
                        kind="video",
                        url=str(selected["url"]),
                        cover=pick_thumbnail(entry),
                        duration=_float_or_zero(entry.get("duration")),
                    )
                )
                continue
            for url in image_urls(entry):
                medias.append(InstagramMedia(kind="image", url=url))

        if not medias:
            raise ParseException("Instagram 作品没有可发送的媒体")

        author = _text(info.get("channel")) or _text(info.get("uploader"))
        title = author or _text(info.get("description")) or _text(info.get("title"))
        author_id = _text(info.get("uploader_id"))
        timestamp = info.get("timestamp")
        cover = next(
            (item.cover for item in medias if item.kind == "video" and item.cover),
            None,
        ) or next(
            (item.url for item in medias if item.kind == "image"),
            None,
        )
        return InstagramPost(
            target=target,
            title=title,
            author=author,
            author_id=author_id,
            text=_text(info.get("description")),
            timestamp=int(str(timestamp)) if timestamp else None,
            cover=cover,
            medias=tuple(medias),
        )

    async def _send_description(self, post: InstagramPost) -> None:
        author = None
        if post.author:
            author = self.create_author(post.author, uid=post.author_id)
        if post.has_video:
            video = next(item for item in post.medias if item.kind == "video")
            await self.send_info(
                title=post.title or "Instagram 视频",
                card_type="video",
                author=author,
                cover_url=post.cover,
                video_duration=f"{int(video.duration)}秒" if video.duration > 0 else None,
                text=post.text or "",
                url=post.target.url,
            )
            return
        await self.send_info(
            title=post.title or "Instagram 图文",
            card_type="common",
            author=author,
            cover_url=post.cover,
            text=post.text or "",
            url=post.target.url,
        )

    def _build_contents(self, post: InstagramPost) -> list[ContentItem]:
        contents: list[ContentItem] = []
        video_index = 0
        for media in post.medias:
            if media.kind == "video":
                video_index += 1
                current_index = video_index

                async def _load_video(idx: int = current_index):
                    target_url = post.target.url
                    if idx > 1:
                        target_url = f"{target_url}?img_index={idx}"
                    fmt = format_selector(self.get_config_value("video_quality", "auto"))
                    return await self.downloader.download_with_ytdlp(
                        target_url,
                        proxy=self.proxy,
                        custom_opts={"format": fmt},
                    )

                from ...utils.models import VideoContent

                contents.append(
                    VideoContent(
                        None,
                        _load_video,
                        duration=media.duration,
                    )
                )
                continue
            contents.extend(self.create_image_contents([media.url], headers=self.ig_headers, proxy=self.proxy))
        return contents


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _int_or_zero(value: object) -> int:
    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value: object) -> float:
    try:
        return float(str(value or 0))
    except (TypeError, ValueError):
        return 0.0
