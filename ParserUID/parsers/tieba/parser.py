"""百度贴吧帖子与多媒体解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from collections.abc import Sequence

from ..base import BaseParser, handle
from .content import (
    IMAGE_HOSTS,
    Fragment,
    CommentInfo,
    decode_page,
    build_metrics,
    normalize_media_url,
)
from .request import TiebaClient
from ...sender import ContentItem
from ...utils.models import Platform, TextContent
from ...render.models import Comment, CommentReply
from ...utils.exceptions import ParseException

_REFERER = "https://tieba.baidu.com/"


def comment_limit(value: object) -> int:
    try:
        return max(0, min(int(str(value or 5)), 20))
    except (TypeError, ValueError):
        return 5


class TiebaParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="tieba", display_name="百度贴吧")

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.client: TiebaClient | None = None

    async def _get_tieba_client(self) -> TiebaClient:
        if self.client is None:
            http_client = await self.get_client()
            self.client = TiebaClient(client=http_client, retries=2)
        return self.client

    @handle("tieba.baidu.com/p/", r"tieba\.baidu\.com/p/(?P<post_id>\d+)")
    async def _parse_post(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        post_id = int(searched.group("post_id"))
        client = await self._get_tieba_client()
        result = await client.fetch_page(post_id)
        limit = (
            comment_limit(self.get_config_value("comment_limit", "5"))
            if bool(self.get_config_value("include_comments", "true").lower() == "true")
            else 0
        )
        info = decode_page(result, comment_limit=limit)
        if not info.author.name:
            raise ParseException("贴吧作者信息为空")

        contents = self._contents(info.contents)
        comments = self._comments(info.comments)
        author = self.create_author(
            info.author.name,
            avatar_url=info.author.avatar_url or None,
            uid=str(info.author.user_id or "") or None,
        )
        cover = self._first_image_url(info.contents)
        forum_info = f"来自：{info.forum_name}吧" if info.forum_name else None

        has_video = any(item.kind == "video" for item in info.contents)
        await self.send_info(
            title=info.title,
            card_type="video" if has_video else "common",
            author=author,
            cover_url=cover,
            url=f"https://tieba.baidu.com/p/{info.tid or post_id}",
            extra_info=forum_info,
            metrics=build_metrics(info),
            comments=comments,
        )
        return self.result(contents)

    def _contents(self, fragments: list[Fragment]) -> list[ContentItem]:
        output: list[ContentItem] = []
        for item in fragments:
            if item.kind == "text":
                if item.text:
                    output.append(TextContent(item.text))
                continue
            if item.kind == "emoji":
                if item.text:
                    output.append(TextContent(item.text))
                continue
            if item.kind == "link":
                text = item.text or item.url
                if text:
                    output.append(TextContent(text))
                continue
            if item.kind == "image" and item.image is not None:
                url = normalize_media_url(item.image.url, hosts=IMAGE_HOSTS)
                if url:
                    output.extend(
                        self.create_image_contents(
                            [url],
                            headers={"Referer": _REFERER},
                            proxy=self.proxy,
                        )
                    )
                continue
            if item.kind == "video" and item.video is not None:
                output.append(
                    self.create_video_content(
                        item.video.url,
                        duration=item.video.duration,
                        headers={"Referer": _REFERER},
                        proxy=self.proxy,
                    )
                )
        return output

    def _comments(self, comments: list[CommentInfo]) -> tuple[Comment, ...]:
        output: list[Comment] = []
        for item in comments:
            replies: list[CommentReply] = []
            for reply in item.replies[:3]:
                reply_texts = [f.text for f in reply.contents if f.text]
                replies.append(
                    CommentReply(
                        author_name=reply.user.name or "贴吧用户",
                        text=" ".join(reply_texts),
                        avatar_url=reply.user.avatar_url or None,
                    )
                )
            root_texts = [f.text for f in item.contents if f.text]
            output.append(
                Comment(
                    author_name=item.user.name or "贴吧用户",
                    text=" ".join(root_texts),
                    avatar_url=item.user.avatar_url or None,
                    timestamp=str(item.timestamp) if item.timestamp else None,
                    location=item.user.ip or None,
                    like_count=str(item.like_count) if item.like_count else None,
                    reply_count=str(item.reply_count) if item.reply_count else None,
                    replies=tuple(replies),
                )
            )
        return tuple(output)

    @staticmethod
    def _first_image_url(fragments: list[Fragment]) -> str | None:
        for item in fragments:
            if item.kind == "image" and item.image is not None:
                url = normalize_media_url(item.image.url, hosts=IMAGE_HOSTS)
                if url:
                    return url
        return None
