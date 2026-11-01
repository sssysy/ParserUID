"""酷安动态链接解析器。"""

from __future__ import annotations

import re
import asyncio
from typing import Literal, ClassVar
from collections.abc import Sequence

import httpx

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .content import (
    feed_text,
    build_comments,
    decode_feed_page,
    format_timestamp,
    decode_reply_page,
    normalize_media_url,
    normalize_media_urls,
)
from ...sender import ContentItem
from ...utils.models import Platform, TextContent
from ...render.models import Comment
from ...utils.helpers import build_image_blocks
from ...utils.exceptions import ParseException

_MIRROR_HOST: Literal["https://www.coolapk1s.com"] = "https://www.coolapk1s.com"
_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_MEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.coolapk.com/",
}


class CoolapkParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="coolapk", display_name="酷安")

    @handle("coolapk1s.com/feed/", r"(?:www\.)?coolapk1s\.com/feed/(?P<feed_id>\d+)")
    @handle("www.coolapk.com/feed/", r"(?:www\.)?coolapk\.com/feed/(?P<feed_id>\d+)")
    @handle("coolapk.com/feed/", r"(?:www\.)?coolapk\.com/feed/(?P<feed_id>\d+)")
    async def _parse_feed(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        feed_id = searched.group("feed_id")
        feed_url = f"{_MIRROR_HOST}/feed/{feed_id}"
        feed_html = await self._fetch_text(
            feed_url,
            referer="https://www.coolapk.com/",
            error="获取酷安动态失败",
        )
        page = decode_feed_page(feed_html)
        feed = page.props.page_props.feed
        if feed is None:
            raise ParseException("酷安动态不存在或已删除")

        text = feed_text(feed)
        image_urls = normalize_media_urls(feed.pic_arr or [])
        if not text and not image_urls:
            raise ParseException("酷安动态内容为空")

        comments = await self._comments(feed_id) if self.config.get_bool("include_comments", True) else ()
        author = self.create_author(
            feed.username or "酷安用户",
            avatar_url=normalize_media_url(feed.user_avatar) or None,
            avatar_headers=_MEDIA_HEADERS,
        )
        title = feed.message_title.strip() or feed.title.strip() or "酷安动态"
        extra_info: list[str] = []
        summary = page.props.page_props.ai_summary
        if summary:
            extra_info.append(f"AI 摘要：{summary}")
        if image_urls:
            extra_info.append(f"图片 {len(image_urls)} 张")

        await self.send_info(
            title=title,
            card_type="common",
            author=author,
            text=text,
            url=f"https://www.coolapk.com/feed/{feed_id}",
            timestamp=format_timestamp(feed.dateline),
            extra_info=" · ".join(extra_info) if extra_info else None,
            blocks=build_image_blocks(
                image_urls,
                limit=self.card_image_limit,
                headers=_MEDIA_HEADERS,
            ),
            comments=comments,
        )
        logger.info(f"[Parser·酷安] 解析完成 | feed_id={feed_id} | images={len(image_urls)} | comments={len(comments)}")

        contents: list[ContentItem] = []
        if text and self.config.get_bool("show_body_text", True):
            contents.append(TextContent(text))
        contents.extend(
            self.create_image_contents(
                image_urls,
                headers=_MEDIA_HEADERS,
                proxy=self.proxy,
            )
        )
        return self.result(contents)

    async def _comments(self, feed_id: str) -> tuple[Comment, ...]:
        limit = max(0, min(self.config.get_int("comment_limit", 5), 20))
        if limit == 0:
            return ()
        try:
            reply_html = await self._fetch_text(
                f"{_MIRROR_HOST}/reply/{feed_id}",
                referer=f"{_MIRROR_HOST}/feed/{feed_id}",
                error="获取酷安评论失败",
            )
            page = decode_reply_page(reply_html)
            return build_comments(page.props.page_props.replies, limit=limit)
        except ParseException as exc:
            logger.warning(f"[Parser·酷安] 评论获取失败 | feed_id={feed_id} | error_type={type(exc).__name__}")
            return ()

    async def _fetch_text(self, url: str, *, referer: str, error: str) -> str:
        client = await self.get_client()
        headers = {**_WEB_HEADERS, "Referer": referer}
        retries = 2
        for attempt in range(retries + 1):
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                if attempt < retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise ParseException(error) from exc
            if response.status_code == 404:
                raise ParseException("酷安动态不存在或已删除")
            if response.status_code >= 500 and attempt < retries:
                await asyncio.sleep(1 + attempt)
                continue
            if response.status_code >= 400:
                raise ParseException(f"{error}：HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                raise ParseException(f"{error}：接口返回非 HTML")
            return response.text
        raise ParseException(error)
