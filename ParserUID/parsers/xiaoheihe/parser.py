"""小黑盒社区动态与游戏百科链接解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import urlparse
from collections.abc import Sequence

from .game import (
    GameContent,
    canonical_game_url,
    build_game_content_from_info,
)
from ..base import BaseParser, handle
from .content import PostContent, build_post_content
from .request import XiaoheiheClient
from ...sender import ContentItem
from ...utils.models import Platform, TextContent
from ...utils.exceptions import ParseException


class XiaoheiheParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="xiaoheihe", display_name="小黑盒")

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.headers: dict[str, str] = {
            "accept": "application/json, text/plain, */*",
            "referer": "https://www.xiaoheihe.cn/",
            "origin": "https://www.xiaoheihe.cn",
        }
        cookie = self.get_config_value("cookie")
        if cookie:
            self.headers["cookie"] = cookie
        self.client = XiaoheiheClient(
            headers=self.headers,
            timeout=30,
            retries=2,
        )

    async def close(self) -> None:
        await self.client.close()
        await super().close()

    @handle(
        "xiaoheihe.cn/app/bbs/link",
        r"xiaoheihe\.cn/app/bbs/link/(?P<link_id>[0-9a-z]+)",
    )
    @handle(
        "xiaoheihe.cn/bbs/post_share",
        r"xiaoheihe\.cn/bbs/post_share\?[A-Za-z0-9._%&+=/#@?;-]*link_id=(?P<link_id>[0-9a-z]+)",
    )
    @handle(
        "api.xiaoheihe.cn/v3/bbs/app/api/web/share",
        r"api\.xiaoheihe\.cn/v3/bbs/app/api/web/share\?[A-Za-z0-9._%&+=/#@?;-]*link_id=(?P<link_id>[0-9a-z]+)",
    )
    async def _parse_post(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        link_id = searched.groupdict().get("link_id") or ""
        result = await self.client.fetch_link_tree(link_id)
        raw_link = result.get("link")
        link = raw_link if isinstance(raw_link, dict) else {}
        if not link:
            raise ParseException("小黑盒帖子内容为空")
        content = build_post_content(
            link,
            original_image=bool(self.get_config_value("use_original_image", "true").lower() == "true"),
        )
        return await self._finish_post(content, link_id)

    @handle(
        "xiaoheihe.cn/app/topic/game",
        r"xiaoheihe\.cn/app/topic/game/(?P<game_type>[a-z]+)/(?P<appid>[0-9a-z]+)",
    )
    @handle(
        "api.xiaoheihe.cn/game/share_game_detail",
        r"api\.xiaoheihe\.cn/game/share_game_detail\?[A-Za-z0-9._%&+=/#@?;-]*appid=(?P<appid>[0-9a-z]+)[A-Za-z0-9._%&+=/#@?;-]*game_type=(?P<game_type>[a-z]+)",
    )
    async def _parse_game(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        groups = searched.groupdict()
        appid = (groups.get("appid") or "").strip()
        game_type = (groups.get("game_type") or "").strip().lower() or "pc"
        if not appid:
            raise ParseException("无效的小黑盒游戏 appid")
        url = canonical_game_url(appid, game_type)
        game = await self.client.fetch_game_info(appid)
        intro: dict[str, object] = {}
        steam_appid = _steam_appid(game, appid)
        if steam_appid:
            intro = await self.client.fetch_game_intro(steam_appid)
        videos: list[tuple[str, str | None]] = []
        content = build_game_content_from_info(game, appid, game_type, intro, videos)
        return await self._finish_game(content, url)

    async def _finish_post(self, content: PostContent, link_id: str) -> Sequence[ContentItem]:
        url = f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}"
        show_text = bool(self.get_config_value("show_body_text", "true").lower() == "true")
        mixed = self.get_config_value("mixed_layout", "true").lower() == "true"
        contents: list[ContentItem] = []
        if mixed:
            for kind, value, extra in content.segments:
                if kind == "text":
                    if show_text:
                        contents.append(TextContent(value))
                elif kind == "live":
                    contents.append(
                        self.create_live_photo_content(
                            extra,
                            value,
                            cover=None,
                            headers=self.headers,
                        )
                    )
                else:
                    contents.extend(self.create_image_contents([value], headers=self.headers))
            if content.video_url:
                contents.append(self._video_content(content.video_url))
        else:
            for kind, value, extra in content.segments:
                if kind == "image":
                    contents.extend(self.create_image_contents([value], headers=self.headers))
                elif kind == "live":
                    contents.append(
                        self.create_live_photo_content(
                            extra,
                            value,
                            cover=None,
                            headers=self.headers,
                        )
                    )
            if content.video_url:
                contents.append(self._video_content(content.video_url))
            if show_text:
                for kind, value, _ in content.segments:
                    if kind == "text":
                        contents.append(TextContent(value))
        cover = (
            content.video_cover
            or next(iter(content.image_urls), None)
            or (content.live_entries[0][1] if content.live_entries else None)
        )
        info: list[str] = []
        if content.image_urls:
            info.append(f"包含图片 {len(content.image_urls)} 张")
        if content.live_entries:
            info.append(f"实况图 {len(content.live_entries)} 张")
        if content.video_url:
            info.append("包含视频 1 条")
        author = self.create_author(
            content.author_name,
            avatar_url=content.author_avatar,
            description=content.author_description,
            uid=content.author_uid,
        )
        await self.send_info(
            title=content.title,
            card_type="video" if content.video_url else "common",
            author=author,
            cover_url=cover,
            text="" if show_text else (content.body_text or ""),
            url=url,
            extra_info=" · ".join(info) if info else None,
            metrics=tuple(content.metrics),
        )
        return self.result(contents)

    async def _finish_game(self, content: GameContent, url: str) -> Sequence[ContentItem]:
        show_text = bool(self.get_config_value("show_body_text", "true").lower() == "true")
        contents: list[ContentItem] = []
        contents.extend(self.create_image_contents(content.image_urls, headers=self.headers))
        for video_url, cover in content.video_urls:
            contents.append(self._video_content(video_url))
        if show_text and content.description:
            contents.append(TextContent(content.description))
        info: list[str] = []
        if content.image_urls:
            info.append(f"游戏图片 {len(content.image_urls)} 张")
        if content.video_urls:
            info.append(f"游戏视频 {len(content.video_urls)} 条")
        await self.send_info(
            title=content.title,
            card_type="common",
            author=None,
            cover_url=next(iter(content.image_urls), None),
            text="" if show_text else (content.description or ""),
            url=url,
            extra_info=" · ".join(info) if info else None,
        )
        return self.result(contents)

    def _video_content(self, url: str) -> ContentItem:
        path = (urlparse(url).path or "").lower()
        if path.endswith(".m3u8"):

            async def load_video():
                return await self.downloader.download_with_ytdlp(url)

            from ...utils.models import VideoContent

            return VideoContent(None, load_video)
        return self.create_video_content(
            url,
            headers=self.headers,
        )


def _steam_appid(game: dict[str, object], fallback: str) -> int | None:
    try:
        return int(str(game.get("steam_appid") or fallback))
    except (TypeError, ValueError):
        return None
