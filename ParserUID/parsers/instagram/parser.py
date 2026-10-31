"""Instagram 帖子、Reels 与视频链接解析器。"""

from __future__ import annotations

import re
import asyncio
import tempfile
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .media import (
    InstagramPost,
    InstagramMedia,
    InstagramTarget,
    image_urls,
    media_title,
    parse_target,
    author_avatar,
    video_formats,
    pick_thumbnail,
    unwrap_entries,
    format_selector,
)
from ...config import card_image_limit
from ...sender import ContentItem
from ...utils.models import Platform, VideoContent
from ...render.models import ImageBlock
from ...utils.helpers import build_image_blocks
from ...utils.exceptions import ParseException, DownloadException

COOKIE_DOMAIN = ".instagram.com"


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
        cookie = self.get_config_value("cookie")
        cookie_file = self._cookie_file(cookie)
        try:
            return await self.downloader.ytdlp_extract_raw(
                target.url,
                cookiefile=cookie_file,
                headers=self.ig_headers,
                proxy=self.proxy,
                ignore_no_formats_error=True,
            )
        except (DownloadException, OSError, ValueError) as exc:
            logger.warning(f"[instagram] 元数据获取失败 | error={exc} | shortcode={target.shortcode}")
            raise ParseException("Instagram 作品信息获取失败") from exc
        finally:
            if cookie_file is not None:
                await asyncio.to_thread(cookie_file.unlink, missing_ok=True)

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
                        title=media_title(entry),
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
            author_avatar=author_avatar(info),
            text=_text(info.get("description")),
            timestamp=int(str(timestamp)) if timestamp else None,
            cover=cover,
            medias=tuple(medias),
        )

    async def _send_description(self, post: InstagramPost) -> None:
        author = None
        if post.author:
            author = self.create_author(
                post.author,
                avatar_url=post.author_avatar,
                uid=post.author_id,
            )
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
                timestamp=post.timestamp,
            )
            return
        await self.send_info(
            title=post.title or "Instagram 图文",
            card_type="common",
            author=author,
            cover_url=post.cover,
            text=post.text or "",
            url=post.target.url,
            timestamp=post.timestamp,
            blocks=self._image_blocks(post),
        )

    def _build_contents(self, post: InstagramPost) -> list[ContentItem]:
        return self._media_contents(post)

    def _image_blocks(self, post: InstagramPost) -> tuple[ImageBlock, ...]:
        """轮播图文按原顺序进入卡片，视频条目不参与图文卡片。"""
        urls = [media.url for media in post.medias if media.kind == "image" and media.url]
        return build_image_blocks(urls, limit=card_image_limit())

    def _media_contents(self, post: InstagramPost) -> list[ContentItem]:
        contents: list[ContentItem] = []
        for media in post.medias:
            if media.kind == "video":

                async def _load_video(media_url: str = media.url) -> Path:
                    return await self._download_media(media_url)

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

    async def _download_media(self, url: str) -> Path:
        """直接下载轮播条目的真实媒体地址，避免重解同一作品。"""
        headers = self.ig_headers
        try:
            return await self.downloader.download_video(
                url,
                headers=headers,
                proxy=self.proxy,
            )
        except DownloadException:
            pass

        custom_opts: dict[str, object] = {
            "format": format_selector(self.get_config_value("video_quality", "auto")),
        }
        cookie_file = self._cookie_file(self.get_config_value("cookie"))
        try:
            return await self.downloader.download_with_ytdlp(
                url,
                proxy=self.proxy,
                cookiefile=cookie_file,
                custom_opts=custom_opts,
            )
        except DownloadException as exc:
            logger.warning(f"[instagram] 视频下载失败 | error={exc}")
            raise ParseException("Instagram 视频下载失败") from exc
        finally:
            if cookie_file is not None:
                await asyncio.to_thread(cookie_file.unlink, missing_ok=True)

    @staticmethod
    def _cookie_file(cookie: str) -> Path | None:
        """将配置 Cookie 写入受限的临时 Netscape 文件供 yt-dlp 读取。"""
        if not cookie.strip():
            return None
        file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".cookies.txt",
            prefix="instagram_",
            delete=False,
        )
        path = Path(file.name)
        try:
            file.write("# Netscape HTTP Cookie File\n")
            for raw_pair in cookie.split(";"):
                name, separator, value = raw_pair.strip().partition("=")
                if not separator or not name or not value:
                    continue
                file.write(f"{COOKIE_DOMAIN}\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")
        finally:
            file.close()
        return path


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
