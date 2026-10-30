"""Telegram 平台频道消息与多媒体链接解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from collections.abc import Sequence

from gsuid_core.logger import logger

from .page import parse_page_html
from ..base import BaseParser, handle
from .models import TelegramPost
from ...sender import ContentItem
from ...utils.models import Platform, TextContent
from ...render.models import Metric
from ...utils.exceptions import ParseException

CHANNEL_PATTERN = r"(?P<channel>[A-Za-z][A-Za-z0-9_]{3,31})"
POST_PATTERN = r"(?P<message_id>\d+)"
URL_PREFIX = r"(?<![A-Za-z0-9.-])https?://"
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


def _reserved_pattern() -> str:
    return "|".join(re.escape(item) for item in RESERVED_CHANNELS)


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
    async def _parse(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        channel = searched.groupdict().get("channel") or ""
        post_id = int(searched.groupdict().get("message_id") or 0)
        post = await self._load_post(channel, post_id)
        if post is None:
            raise ParseException("Telegram 帖子不存在、已删除或当前不可见")

        await self._send_description(post)
        contents = self._build_contents(post)
        return self.result(contents)

    async def _load_post(
        self,
        channel: str,
        post_id: int,
    ) -> TelegramPost | None:
        candidates = (
            f"https://t.me/s/{channel}/{post_id}",
            f"https://t.me/{channel}/{post_id}?embed=1&single=",
        )
        last_error: Exception | None = None
        for url in candidates:
            try:
                client = await self.get_client()
                response = await client.get(url, headers=self.tg_headers)
                if response.status_code >= 400:
                    continue
                html_text = response.text
                post = parse_page_html(
                    html_text,
                    channel=channel,
                    post_id=post_id,
                )
                if post is not None:
                    return post
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            logger.warning(f"[telegram] 页面获取失败 | channel={channel} | post={post_id} | error={last_error}")
        return None

    async def _send_description(self, post: TelegramPost) -> None:
        author = self.create_author(
            post.channel_title,
            avatar_url=post.channel_avatar,
            description=post.channel_description,
            uid=f"@{post.channel}",
        )
        metrics: list[Metric] = []
        if post.views:
            metrics.append(Metric(label="浏览", value=post.views))

        video = next(
            (item for item in post.media if item.kind == "video"),
            None,
        )
        cover_url = next(
            (item.thumb or item.url for item in post.media if item.thumb or item.url),
            None,
        )

        await self.send_info(
            title=post.channel_title or "Telegram 频道",
            card_type="video" if video else "common",
            author=author,
            cover_url=cover_url,
            text=post.text or "",
            url=f"https://t.me/{post.channel}/{post.post_id}",
            metrics=tuple(metrics),
        )

    def _build_contents(self, post: TelegramPost) -> list[ContentItem]:
        contents: list[ContentItem] = []
        for item in post.media:
            if item.kind == "image":
                contents.extend(self.create_image_contents([item.url], headers=self.tg_headers))
            elif item.kind == "video":
                contents.append(
                    self.create_video_content(
                        item.url,
                        duration=item.duration,
                        headers=self.tg_headers,
                    )
                )
        if post.text and not contents:
            contents.append(TextContent(post.text))
        return contents
