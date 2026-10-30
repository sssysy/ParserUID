"""抖音链接解析器实现。"""

from __future__ import annotations

import re
from typing import ClassVar

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    Aweme,
    CommentResponse,
    decode_aweme,
    select_video,
)
from ...utils.utils import format_num
from ...utils.models import Platform, MediaContent
from ...render.models import Comment
from ...utils.exceptions import ParseException


class DouYinParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="douyin", display_name="抖音")

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._ttwid: str = ""

    def _get_headers(self, referer: str = "https://www.douyin.com/") -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Referer": referer,
        }
        cookie = self.get_config_value("cookie")
        cookie_parts: list[str] = []
        if cookie:
            cookie_parts.append(cookie)
        if self._ttwid:
            cookie_parts.append(f"ttwid={self._ttwid}")
        if cookie_parts:
            headers["Cookie"] = "; ".join(cookie_parts)
        return headers

    async def _ensure_ttwid(self) -> None:
        if self._ttwid:
            return
        client = await self.get_client()
        try:
            resp = await client.head("https://www.douyin.com/", headers={"User-Agent": "Mozilla/5.0"})
            cookies = resp.cookies
            for name, val in cookies.items():
                if name == "ttwid":
                    self._ttwid = val
                    break
        except Exception:
            pass

    @handle("v.douyin", r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
    @handle("jx.douyin", r"jx\.douyin\.com/[a-zA-Z0-9_\-]+")
    async def _parse_short_link(self, searched: re.Match[str]) -> list[MediaContent]:
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    @handle("aweme_id", r"aweme_id[=:/s]+(?P<vid>\d{10,})")
    @handle("modal_id", r"modal_id=(?P<vid>\d{10,})")
    @handle("aweme", r"aweme/(?P<vid>\d{10,})")
    @handle("douyin", r"douyin\.com/(?P<ty>video|note|article)/(?P<vid>\d+)")
    @handle("m.douyin", r"m\.douyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    async def _parse_douyin(self, searched: re.Match[str]) -> list[MediaContent]:
        vid = searched.group("vid")
        group_dict = searched.groupdict()
        ty = group_dict.get("ty", "video")

        aweme = await self._fetch_aweme(vid, ty)
        author = self.create_author(
            name=aweme.author.nickname or "抖音用户",
            avatar_url=aweme.avatar_url,
            uid=aweme.author_id,
        )

        card_type = "video" if aweme.is_video else "common"
        comments = await self._fetch_comments(aweme.aweme_id)
        selected = select_video(aweme, self.get_config_value("video_quality", "auto"))

        duration_sec = aweme.video.duration / 1000 if aweme.video and aweme.video.duration > 0 else 0.0
        await self.send_info(
            title=aweme.desc or "抖音作品",
            card_type=card_type,
            author=author,
            cover_url=aweme.cover_url,
            text=aweme.desc,
            url=aweme.short_url,
            metrics=aweme.metrics,
            comments=tuple(comments),
            video_duration=f"{int(duration_sec)}s" if duration_sec > 0 else None,
            video_quality=selected.quality if selected else None,
        )

        contents = self._build_contents(aweme, selected, duration_sec)
        return self.result(contents)

    async def _fetch_aweme(self, aweme_id: str, ty: str) -> Aweme:
        await self._ensure_ttwid()
        client = await self.get_client()

        url = f"https://www.douyin.com/{ty}/{aweme_id}"
        resp = await client.get(url, headers=self._get_headers())
        if resp.status_code < 400:
            match = re.search(r'<script id="RENDER_DATA" type="application/json">([^<]+)</script>', resp.text)
            if match is not None:
                import urllib.parse

                raw_json = urllib.parse.unquote(match.group(1))
                try:
                    return decode_aweme(raw_json, detail=False)
                except Exception:
                    pass

        api_url = f"https://www.iesdouyin.com/aweme/v1/web/aweme/detail/?aweme_id={aweme_id}"
        resp_api = await client.get(api_url, headers=self._get_headers(referer="https://www.iesdouyin.com/"))
        if resp_api.status_code >= 400:
            raise ParseException(f"获取抖音作品详情失败：HTTP {resp_api.status_code}")
        return decode_aweme(resp_api.content, detail=True)

    def _build_contents(self, aweme: Aweme, selected, duration: float) -> list[MediaContent]:
        if aweme.is_video and selected is not None:
            primary_url = selected.url
            fallback_urls = selected.fallback_urls
            return [
                self.create_video_content(
                    primary_url,
                    cover=None,
                    duration=duration,
                    fallback_urls=fallback_urls,
                )
            ]

        if aweme.images:
            live_items: list[MediaContent] = []
            pure_img_urls: list[str] = []
            for img in aweme.images:
                if img.video and img.video.play_addr.urls and img.url_list:
                    live_v_url = img.video.play_addr.urls[0]
                    live_img_url = img.url_list[0]
                    live_items.append(
                        self.create_live_photo_content(
                            live_v_url,
                            live_img_url,
                            duration=img.video.duration / 1000 if img.video.duration > 0 else 0.0,
                        )
                    )
                elif img.url_list:
                    pure_img_urls.append(img.url_list[0])

            if live_items:
                return live_items
            if pure_img_urls:
                return [c for c in self.create_image_contents(pure_img_urls)]

        return []

    async def _fetch_comments(self, aweme_id: str) -> list[Comment]:
        if self.get_config_value("include_comments", "false").lower() != "true":
            return []
        limit_str = self.get_config_value("comment_limit", "5")
        limit = int(limit_str) if limit_str.isdigit() else 5
        if limit <= 0:
            return []

        client = await self.get_client()
        api = f"https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id={aweme_id}&count={limit}"
        try:
            resp = await client.get(api, headers=self._get_headers())
            if resp.status_code >= 400:
                return []
            import msgspec

            res = msgspec.json.decode(resp.content, type=CommentResponse)
            items = res.comments or []
            results: list[Comment] = []
            for c in items[:limit]:

                def _to_int(v: int | str | None) -> int:
                    try:
                        return int(v or 0)
                    except Exception:
                        return 0

                results.append(
                    Comment(
                        author_name=c.user.nickname or "抖音用户",
                        text=c.text,
                        avatar_url=c.user.avatar_thumb.url_list[0] if c.user.avatar_thumb.url_list else None,
                        location=c.ip_label,
                        like_count=format_num(_to_int(c.digg_count)),
                        reply_count=format_num(_to_int(c.reply_comment_total)),
                    )
                )
            return results
        except Exception as exc:
            logger.warning(f"[抖音] 获取评论失败 | aweme_id={aweme_id} | error={exc}")
            return []
