"""快手链接解析器实现。"""

from __future__ import annotations

import re
from string import digits, ascii_letters
from typing import ClassVar
from secrets import choice
from urllib.parse import urlparse

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    Photo,
    decode_comments,
    decode_init_state,
    build_photo_metrics,
)
from ...config import card_image_limit
from ...utils.models import Platform, MediaContent
from ...render.models import Comment, ImageBlock
from ...utils.helpers import build_image_blocks
from ...utils.exceptions import ParseException


class KuaiShouParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="kuaishou", display_name="快手")

    INIT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"window\.INIT_STATE\s*=\s*(.*?)</script>", re.DOTALL)
    COMMENT_API: ClassVar[str] = "https://kph8gvfz.m.chenzhongtech.com/rest/wd/photo/comment/list"

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
            ),
            "Referer": "https://v.kuaishou.com/",
        }
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @handle(
        "v.kuaishou",
        r"(?:(?:https?://)|^)(?P<host>v\.kuaishou\.com)/(?P<path>[A-Za-z0-9_-]+)(?:\?[^\s#]*)?",
    )
    @handle(
        "kuaishou",
        r"(?:(?:https?://)|^)(?P<host>(?:www\.)?kuaishou\.com)/(?:(?:f|short-video|fw/photo|photo)/(?P<path>[A-Za-z0-9_-]+))(?:\?[^\s#]*)?",
    )
    @handle(
        "chenzhongtech",
        r"(?:(?:https?://)|^)(?P<host>(?:v\.m\.)?chenzhongtech\.com)/fw/(?:photo|long-video)/(?P<path>[A-Za-z0-9_-]+)(?:\?[^\s#]*)?",
    )
    @handle(
        "gifshow",
        r"(?:(?:https?://)|^)(?P<host>m\.gifshow\.com)/fw/photo/(?P<path>[A-Za-z0-9_-]+)(?:\?[^\s#]*)?",
    )
    async def _parse(self, searched: re.Match[str]) -> list[MediaContent]:
        url = self._matched_url(searched)
        photo = await self._fetch_photo(url)
        contents = self._build_contents(photo)
        if not contents:
            raise ParseException("快手作品缺少可发送的媒体")

        author = self.create_author(
            name=photo.author_name,
            avatar_url=photo.head_url,
            uid=str(photo.user_id) if photo.user_id is not None else None,
        )
        comments = await self._fetch_comments(photo.photo_id)
        metrics = build_photo_metrics(photo)
        page_url = f"https://m.gifshow.com/fw/photo/{photo.photo_id}" if photo.photo_id else url
        video_urls = photo.video_urls
        cover_url = photo.cover_urls_list[0] if photo.cover_urls_list else None

        card_type = "video" if video_urls else "common"
        await self.send_info(
            title=photo.caption or "快手作品",
            card_type=card_type,
            author=author,
            cover_url=cover_url,
            text=photo.caption,
            url=page_url,
            metrics=metrics,
            comments=tuple(comments),
            video_duration=f"{int(photo.duration / 1000)}s" if photo.duration > 0 else None,
            blocks=self._image_blocks(photo),
        )
        return self.result(contents)

    @staticmethod
    def _image_blocks(photo: Photo) -> tuple[ImageBlock, ...]:
        """快手图文帖的图片按原顺序进入卡片，视频作品不重复渲染封面。"""
        if photo.video_urls:
            return ()
        return build_image_blocks(photo.image_urls, limit=card_image_limit())

    @staticmethod
    def _matched_url(searched: re.Match[str]) -> str:
        text = searched.group(0)
        return text if text.startswith(("http://", "https://")) else f"https://{text}"

    async def _fetch_photo(self, url: str) -> Photo:
        headers = self._get_headers()
        client = await self.get_client()

        final_url = await self.get_final_url(url, headers=headers)
        final_url = final_url.replace("/fw/long-video/", "/fw/photo/")
        final_url = self._mobile_photo_url(final_url)

        try:
            resp = await client.get(final_url, headers=headers)
            if resp.status_code >= 400:
                raise ParseException(f"获取快手页面失败：HTTP {resp.status_code}")
            html_text = resp.text
        except Exception as exc:
            raise ParseException(f"获取快手页面失败：{exc}") from exc

        matched = self.INIT_PATTERN.search(html_text)
        if matched is None:
            raise ParseException("快手页面缺少作品数据")

        try:
            return decode_init_state(matched.group(1).strip())
        except Exception as exc:
            raise ParseException(f"快手作品数据解析失败：{exc}") from exc

    def _build_contents(self, photo: Photo) -> list[MediaContent]:
        video_urls = photo.video_urls
        if video_urls:
            primary_url = video_urls[0]
            backup_urls = video_urls[1:]
            return [
                self.create_video_content(
                    primary_url,
                    cover=None,
                    duration=photo.duration / 1000 if photo.duration > 0 else 0.0,
                    proxy=self.proxy,
                    fallback_urls=backup_urls,
                )
            ]
        image_urls = photo.image_urls
        if image_urls:
            return [c for c in self.create_image_contents(image_urls, proxy=self.proxy)]
        return []

    @staticmethod
    def _mobile_photo_url(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path_parts = [part for part in parsed.path.split("/") if part]
        if host in {"www.kuaishou.com", "kuaishou.com"} and len(path_parts) >= 2:
            photo_id = path_parts[1] if path_parts[0] in {"short-video", "photo"} else ""
            if photo_id:
                return f"https://m.gifshow.com/fw/photo/{photo_id}"
        return url

    async def _fetch_comments(self, photo_id: str) -> list[Comment]:
        max_comments_cfg = self.get_config_value("max_comments", "5")
        limit = int(max_comments_cfg) if max_comments_cfg.isdigit() else 5
        if limit <= 0 or not photo_id:
            return []

        headers = self._get_headers()
        headers["Referer"] = f"https://m.gifshow.com/fw/photo/{photo_id}"
        did = "web_" + "".join(choice(ascii_letters + digits) for _ in range(32))
        cookie = headers.get("Cookie", "")
        headers["Cookie"] = f"{cookie}; did={did}".strip("; ")

        client = await self.get_client()
        try:
            resp = await client.post(
                self.COMMENT_API,
                json={"photoId": photo_id, "count": limit},
                headers=headers,
            )
            if resp.status_code >= 400:
                return []
            return decode_comments(resp.content, limit=limit)
        except Exception as exc:
            logger.warning(f"[快手] 获取评论失败 | photo_id={photo_id} | error={exc}")
            return []
