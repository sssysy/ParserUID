"""Telegram 平台频道消息与多媒体链接解析器。"""

from __future__ import annotations

import re
import asyncio
from typing import ClassVar
from pathlib import Path
from urllib.parse import urlparse
from collections.abc import Sequence

import httpx

from gsuid_core.logger import logger

from .page import parse_page_html
from ..base import BaseParser, handle
from .models import TelegramPost, TelegramMedia
from ...config import card_image_limit
from ...sender import ContentItem
from ...utils.models import Platform, TextContent
from ...render.models import Author, Metric, ImageBlock, PreviewCard
from ...utils.helpers import build_image_blocks
from ...utils.exceptions import ParseException

CHANNEL_PATTERN = r"(?P<channel>[A-Za-z][A-Za-z0-9_]{2,31})"
POST_PATTERN = r"(?P<message_id>\d{1,10})(?![0-9])"
URL_PREFIX = r"(?<![\w./%?=&-])https?://"
PAGE_HOST_SUFFIXES = ("t.me", "telegram.me", "telegram.dog")
RESERVED_CHANNELS = (
    "s",
    "c",
    "m",
    "addstickers",
    "addtheme",
    "addemoji",
    "joinchat",
    "contact",
    "share",
    "invoice",
    "socks",
    "proxy",
    "setlanguage",
    "bg",
    "login",
)
TRUSTED_MEDIA_SUFFIXES = (
    "telesco.pe",
    "telegram.org",
    "telegram-cdn.org",
    "telegram-cdn.com",
    "t.me",
)
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_EXTRA_TEXT = 500


def _reserved_pattern() -> str:
    return "|".join(re.escape(item) for item in RESERVED_CHANNELS)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == item or host.endswith(f".{item}") for item in suffixes)


def _is_trusted_media_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and _host_matches(host, TRUSTED_MEDIA_SUFFIXES)
    )


def _short(value: str | None, limit: int = MAX_EXTRA_TEXT) -> str | None:
    if not value:
        return None
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def _checked_bool(value: str | bool | None, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return value.strip().lower() == "true"


class TelegramParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name="telegram",
        display_name="Telegram",
    )

    @property
    def tg_headers(self) -> dict[str, str]:
        headers = {
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Referer": "https://t.me/",
        }
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @property
    def media_headers(self) -> dict[str, str]:
        headers = self.tg_headers
        headers["Accept"] = "image/avif,image/webp,image/*,video/*,*/*;q=0.8"
        return headers

    @handle(
        "t.me/s/",
        URL_PREFIX + r"(?:www\.)?t\.me/s/" + CHANNEL_PATTERN + "/" + POST_PATTERN,
    )
    @handle(
        "t.me/",
        URL_PREFIX
        + r"(?:www\.)?t\.me/(?!(?:"
        + _reserved_pattern()
        + r")(?:/|$))"
        + CHANNEL_PATTERN
        + "/"
        + POST_PATTERN,
    )
    @handle(
        "telegram.me/s/",
        URL_PREFIX + r"(?:www\.)?telegram\.me/s/" + CHANNEL_PATTERN + "/" + POST_PATTERN,
    )
    @handle(
        "telegram.me/",
        URL_PREFIX
        + r"(?:www\.)?telegram\.me/(?!(?:"
        + _reserved_pattern()
        + r")(?:/|$))"
        + CHANNEL_PATTERN
        + "/"
        + POST_PATTERN,
    )
    @handle(
        "telegram.dog/s/",
        URL_PREFIX + r"(?:www\.)?telegram\.dog/s/" + CHANNEL_PATTERN + "/" + POST_PATTERN,
    )
    @handle(
        "telegram.dog/",
        URL_PREFIX
        + r"(?:www\.)?telegram\.dog/(?!(?:"
        + _reserved_pattern()
        + r")(?:/|$))"
        + CHANNEL_PATTERN
        + "/"
        + POST_PATTERN,
    )
    async def _parse(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        channel = searched.groupdict().get("channel") or ""
        post_id = int(searched.groupdict().get("message_id") or 0)
        post = await self._load_post(channel, post_id)
        if post is None:
            raise ParseException("Telegram 帖子不存在、已删除或当前不可见")
        if not self._has_content(post):
            raise ParseException("Telegram 帖子没有可解析内容")

        await self._send_description(post)
        contents = self._build_contents(post)
        return self.result(contents)

    async def _load_post(
        self,
        channel: str,
        post_id: int,
    ) -> TelegramPost | None:
        candidates = (
            (f"https://t.me/s/{channel}/{post_id}", None),
            (
                f"https://t.me/{channel}/{post_id}",
                {"embed": "1", "single": ""},
            ),
        )
        last_error: ParseException | None = None
        for url, params in candidates:
            try:
                html_text = await self._fetch_page(url, params=params)
            except ParseException as exc:
                last_error = exc
                continue
            post = await asyncio.to_thread(
                parse_page_html,
                html_text,
                channel=channel,
                post_id=post_id,
            )
            if post is not None:
                return post
        if last_error is not None:
            logger.warning(
                "[Parser·Telegram] 页面获取失败 "
                f"| channel={channel} | post={post_id} "
                f"| error_type={type(last_error).__name__}"
            )
        return None

    async def _fetch_page(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> str:
        client = await self.get_client()
        try:
            response = await client.get(
                url,
                params=params,
                headers=self.tg_headers,
            )
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ParseException("Telegram 公开页面请求失败") from exc
        host = (response.url.host or "").lower()
        if not _host_matches(host, PAGE_HOST_SUFFIXES):
            raise ParseException("Telegram 页面跳转到了不受支持的站点")
        if response.status_code >= 400:
            raise ParseException(f"Telegram 公开页面请求失败：HTTP {response.status_code}")
        if len(response.content) > MAX_PAGE_BYTES:
            raise ParseException("Telegram 页面体积超过限制")
        return response.text

    @staticmethod
    def _has_content(post: TelegramPost) -> bool:
        return bool(post.text or post.sendable_media or post.media or post.preview or post.poll or post.buttons)

    async def _send_description(self, post: TelegramPost) -> None:
        author = self._author(post)
        text = self._description_text(post)
        extra = self._extra(post)
        metrics = self._metrics(post)
        cover_url = self._cover_url(post)
        repost = self._repost(post)
        video = next(
            (item for item in post.sendable_media if item.kind == "video"),
            None,
        )
        if video is not None:
            await self.send_info(
                title="",
                card_type="video",
                author=author,
                cover_url=cover_url,
                text=text,
                timestamp=post.timestamp,
                url=self._post_url(post),
                extra_info=extra,
                metrics=metrics,
                repost=repost,
                video_duration=(self._format_duration(video.duration) if video.duration > 0 else None),
            )
            return
        await self.send_info(
            title="",
            card_type="common",
            author=author,
            cover_url=cover_url,
            text=text,
            timestamp=post.timestamp,
            url=self._post_url(post),
            extra_info=extra,
            metrics=metrics,
            repost=repost,
            blocks=self._image_blocks(post),
        )

    @staticmethod
    def _image_blocks(post: TelegramPost) -> tuple[ImageBlock, ...]:
        """Telegram 图片媒体按原顺序进入卡片，视频仍使用封面。"""
        urls = [item.url or "" for item in post.media if item.kind == "image" and item.sendable]
        return build_image_blocks(urls, limit=card_image_limit())

    def _author(self, post: TelegramPost) -> Author:
        avatar = post.channel_avatar if _is_trusted_media_url(post.channel_avatar) else None
        return self.create_author(
            post.display_author,
            avatar_url=avatar,
            description=post.channel_description,
            uid=f"@{post.channel}",
        )

    @staticmethod
    def _description_text(post: TelegramPost) -> str:
        return post.text or ""

    def _extra(self, post: TelegramPost) -> str | None:
        sections: list[str] = []
        if post.channel_description:
            sections.append(f"频道：{post.channel_description}")
        if post.forwarded is not None:
            source = post.forwarded.author or post.forwarded.name
            forward_line = f"转发自：{source}"
            if post.forwarded.url:
                forward_line += f"\n{post.forwarded.url}"
            sections.append(forward_line)
        if post.preview is not None and _checked_bool(
            self.get_config_value("include_link_preview", "true"),
            True,
        ):
            preview_lines = [f"链接预览：{post.preview.title or post.preview.site_name or '链接'}"]
            preview_description = _short(post.preview.description)
            if preview_description:
                preview_lines.append(preview_description)
            if post.preview.url:
                preview_lines.append(post.preview.url)
            sections.append("\n".join(line for line in preview_lines if line))
        if post.poll is not None and _checked_bool(self.get_config_value("include_poll", "true"), True):
            poll_lines = [f"投票：{post.poll.question}"]
            poll_lines.extend(f"{option.name} {option.percent}%" for option in post.poll.options)
            if post.poll.votes:
                poll_lines.append(f"票数：{post.poll.votes}")
            sections.append("\n".join(poll_lines))
        if post.buttons and _checked_bool(
            self.get_config_value("include_buttons", "true"),
            True,
        ):
            button_lines = ["按钮："]
            button_lines.extend(
                f"{button.title}：{button.url}" if button.url else button.title for button in post.buttons
            )
            sections.append("\n".join(button_lines))
        if post.unsupported_media and _checked_bool(
            self.get_config_value("show_unsupported_media", "true"),
            True,
        ):
            sections.append("网页未提供部分媒体文件，可能需要 Telegram 客户端或登录账号。")
        return "\n\n".join(sections) if sections else None

    @staticmethod
    def _metrics(post: TelegramPost) -> tuple[Metric, ...]:
        metrics: list[Metric] = []
        if post.views:
            metrics.append(Metric(label="浏览", value=post.views))
        metrics.extend(Metric(label=item.label, value=item.count) for item in post.reactions)
        return tuple(metrics)

    @staticmethod
    def _repost(post: TelegramPost) -> PreviewCard | None:
        reply = post.reply
        if reply is None:
            return None
        return PreviewCard(
            platform_name="telegram",
            platform_display_name="Telegram",
            title="引用消息",
            text=reply.text,
            author=Author(name=reply.name),
            source_url=reply.url,
            cover_url=reply.thumb,
        )

    @staticmethod
    def _cover_url(post: TelegramPost) -> str | None:
        for item in post.media:
            for candidate in (item.thumb, item.url):
                if _is_trusted_media_url(candidate):
                    return candidate
        if post.preview is not None and _is_trusted_media_url(post.preview.thumb):
            return post.preview.thumb
        if _is_trusted_media_url(post.channel_avatar):
            return post.channel_avatar
        return None

    def _build_contents(self, post: TelegramPost) -> list[ContentItem]:
        contents: list[ContentItem] = []
        for item in post.media:
            content = self._media_content(item)
            if content is not None:
                contents.append(content)
        if not contents and post.text:
            contents.append(TextContent(post.text))
        return contents

    def _media_content(self, item: TelegramMedia) -> ContentItem | None:
        if not item.sendable or not _is_trusted_media_url(item.url):
            return None
        url = item.url
        if url is None:
            return None
        headers = self.media_headers
        if item.kind == "image":
            return self.create_image_contents([url], headers=headers, proxy=self.proxy)[0]
        if item.kind in {"video", "roundvideo"}:
            return self.create_video_content(
                url,
                duration=item.duration,
                headers=headers,
                proxy=self.proxy,
            )
        if item.kind in {"gif", "dynamic"}:
            return self.create_dynamic_contents([url], headers=headers, proxy=self.proxy)[0]
        if item.kind in {"voice", "audio"}:

            async def load_audio() -> Path:
                return await self.downloader.download_audio(
                    url,
                    headers=headers,
                    proxy=self.proxy,
                )

            return self.create_audio_content(
                loader=load_audio,
                duration=item.duration,
            )
        if item.kind == "file":

            async def load_file() -> Path:
                return await self.downloader.download_file(
                    url,
                    filename=item.name,
                    headers=headers,
                    proxy=self.proxy,
                )

            return self.create_file_content(
                loader=load_file,
                name=item.name,
            )
        return None

    @staticmethod
    def _post_url(post: TelegramPost) -> str:
        return f"https://t.me/{post.channel}/{post.post_id}"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, int(seconds))
        minutes, second = divmod(total, 60)
        return f"{minutes:02d}:{second:02d}"
