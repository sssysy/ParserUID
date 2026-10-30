"""NGA 社区帖子与楼层内容解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from collections.abc import Sequence

from ..base import BaseParser, handle
from .content import parse_post_html
from .request import NgaRequest
from ...sender import ContentItem
from ...utils.models import Platform, TextContent
from ...utils.exceptions import ParseException


class NGAParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="nga", display_name="NGA")

    @property
    def nga_headers(self) -> dict[str, str]:
        return {
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://bbs.nga.cn/",
            "Upgrade-Insecure-Requests": "1",
        }

    async def _get_nga_request(self) -> NgaRequest:
        client = await self.get_client()
        cookie = self.get_config_value("cookie")
        return NgaRequest(
            client=client,
            cookie=cookie,
            headers=self.nga_headers,
            timeout=30,
            retries=2,
        )

    @handle(
        "bbs.nga.cn/read.php",
        r"bbs\.nga\.cn/read\.php\?(?P<query>[^\s#]+)",
    )
    @handle(
        "nga.178.com/read.php",
        r"nga\.178\.com/read\.php\?(?P<query>[^\s#]+)",
    )
    @handle(
        "ngabbs.com/read.php",
        r"ngabbs\.com/read\.php\?(?P<query>[^\s#]+)",
    )
    async def _parse_post(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        source_url = f"https://{searched.group(0)}"
        req = await self._get_nga_request()
        tid, pid = req.parse_identifiers(source_url)
        if not bool(self.get_config_value("include_floor", "true").lower() == "true"):
            pid = None
        url = req.canonical_url(tid, pid)
        response = await req.get(url)
        post = parse_post_html(response.text, page_url=url, pid=pid)
        if not post.segments and not post.title:
            raise ParseException("NGA 页面未找到正文内容")

        contents: list[ContentItem] = []
        show_text = bool(self.get_config_value("show_body_text", "true").lower() == "true")
        mixed_layout = bool(self.get_config_value("mixed_layout", "true").lower() == "true")
        image_urls = [value for kind, value in post.segments if kind == "image"]
        text_parts = [value for kind, value in post.segments if kind == "text"]
        if mixed_layout:
            for kind, value in post.segments:
                if kind == "image":
                    contents.extend(
                        self.create_image_contents(
                            [value],
                            headers=self.nga_headers,
                        )
                    )
                elif show_text:
                    contents.append(TextContent(value))
        else:
            contents.extend(self.create_image_contents(image_urls, headers=self.nga_headers))
            if show_text and text_parts:
                contents.append(TextContent("\n\n".join(text_parts)))

        author = (
            self.create_author(
                post.author,
                uid=post.author_uid,
            )
            if post.author
            else None
        )
        info_parts: list[str] = []
        if post.floor_label:
            info_parts.append(post.floor_label)
        if image_urls:
            info_parts.append(f"包含图片 {len(image_urls)} 张")
        extra_info = " · ".join(info_parts) if info_parts else None

        await self.send_info(
            title=post.title or "NGA 帖子",
            card_type="common",
            author=author,
            cover_url=image_urls[0] if image_urls else None,
            text="" if show_text else (post.body_text or ""),
            url=url,
            extra_info=extra_info,
        )
        return self.result(contents)
