"""抖音链接解析器实现。"""

from __future__ import annotations

import re
import json
import asyncio
from typing import ClassVar

import httpx
import msgspec

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    Room,
    Aweme,
    decode_aweme,
    select_video,
    comment_limit,
    decode_comments,
)
from ...utils.utils import format_num
from ...utils.models import Platform, MediaContent
from ...render.models import Metric, Comment
from ...utils.exceptions import ParseException


class DouYinParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="douyin", display_name="抖音")
    TTWID_REGISTER_URL: ClassVar[str] = "https://ttwid.bytedance.com/ttwid/union/register/"
    DETAIL_URL: ClassVar[str] = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    COMMENT_URL: ClassVar[str] = "https://www.douyin.com/aweme/v1/web/comment/list/"
    LIVE_URL: ClassVar[str] = "https://live.douyin.com/webcast/room/web/enter/"
    WEB_RID_RE: ClassVar[re.Pattern[str]] = re.compile(r'\\"webRid\\":\\"(\d+)\\"')
    COMMON_PARAMS: ClassVar[dict[str, str]] = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "version_code": "190600",
        "version_name": "19.6.0",
        "update_version_code": "170400",
        "pc_client_type": "1",
        "cookie_enabled": "true",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "132.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "8",
        "device_memory": "8",
        "engine_version": "132.0.0.0",
        "platform": "PC",
        "screen_width": "1920",
        "screen_height": "1080",
    }

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._ttwid: str = ""
        self._ttwid_lock = asyncio.Lock()

    def _get_headers(
        self,
        referer: str = "https://www.douyin.com/",
        *,
        include_cookie: bool = True,
    ) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 15; SM-G998B) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Mobile Safari/537.36 Edg/132.0.0.0"
            ),
            "Referer": referer,
        }
        cookie = self.get_config_value("cookie")
        cookie_parts: list[str] = []
        if include_cookie and cookie:
            cookie_parts.append(cookie)
        if include_cookie and self._ttwid:
            cookie_parts.append(f"ttwid={self._ttwid}")
        if cookie_parts:
            headers["Cookie"] = "; ".join(cookie_parts)
        return headers

    async def _ensure_ttwid(self) -> None:
        if self._ttwid:
            return
        async with self._ttwid_lock:
            if self._ttwid:
                return
            await self._register_ttwid()

    async def _register_ttwid(self) -> None:
        client = await self.get_client()
        payload = {
            "region": "cn",
            "aid": 1768,
            "needFid": False,
            "service": "www.douyin.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https",
            "union": True,
        }
        headers = self._get_headers(include_cookie=False)
        headers["Content-Type"] = "application/json"
        try:
            response = await client.post(
                self.TTWID_REGISTER_URL,
                json=payload,
                headers=headers,
            )
            if response.status_code >= 400:
                raise ParseException(f"抖音 ttwid 注册失败：HTTP {response.status_code}")
            data = json.loads(response.content)
            if not isinstance(data, dict):
                raise ParseException("抖音 ttwid 注册响应格式异常")
            redirect_url = data.get("redirect_url")
            if not isinstance(redirect_url, str) or not redirect_url:
                raise ParseException("抖音 ttwid 注册响应缺少回调地址")
            callback_headers = self._get_headers(include_cookie=False)
            self._set_response_ttwid(response.headers.get_list("set-cookie"))
            callback_headers.update(
                {
                    "Cookie": self._cookie_header(),
                    "Referer": "https://www.douyin.com/",
                }
            )
            callback = await client.get(
                redirect_url,
                headers=callback_headers,
            )
            if callback.status_code >= 400:
                raise ParseException(f"抖音 ttwid 回调失败：HTTP {callback.status_code}")
            self._set_response_ttwid(callback.headers.get_list("set-cookie"))
        except ParseException:
            raise
        except httpx.HTTPError as exc:
            raise ParseException("抖音 ttwid 注册请求失败") from exc
        except (ValueError, TypeError):
            raise ParseException("抖音 ttwid 注册响应格式异常")
        if not self._ttwid:
            raise ParseException("抖音 ttwid 注册未返回 Cookie")

    def _set_response_ttwid(self, set_cookie_headers: list[str]) -> None:
        for header in set_cookie_headers:
            for part in header.split(";"):
                name, separator, value = part.strip().partition("=")
                if separator and name == "ttwid" and value:
                    self._ttwid = value
                    return

    def _cookie_header(self) -> str:
        cookie = self.get_config_value("cookie")
        if self._ttwid:
            return "; ".join(item for item in (cookie, f"ttwid={self._ttwid}") if item)
        return cookie

    @handle("v.douyin", r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
    @handle("jx.douyin", r"jx\.douyin\.com/[a-zA-Z0-9_\-]+")
    async def _parse_short_link(self, searched: re.Match[str]) -> list[MediaContent]:
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    @handle("webcast.amemv.com", r"douyin/webcast/reflow/(?P<room_id>\d+)")
    async def _parse_live_by_room_id(self, searched: re.Match[str]) -> list[MediaContent]:
        room_id = searched.group("room_id")
        await self._ensure_ttwid()
        client = await self.get_client()
        response = await client.get(
            f"https://webcast.amemv.com/douyin/webcast/reflow/{room_id}",
            headers=self._get_headers(referer="https://webcast.amemv.com/"),
        )
        if response.status_code >= 400:
            raise ParseException(f"抖音直播页访问失败：HTTP {response.status_code}")
        matched = self.WEB_RID_RE.search(response.text)
        if matched is None:
            raise ParseException("提取抖音直播间 ID 失败")
        return await self._parse_live_id(matched.group(1))

    @handle("live.douyin.com", r"live\.douyin\.com/(?P<rid>\d+)")
    async def _parse_live(self, searched: re.Match[str]) -> list[MediaContent]:
        return await self._parse_live_id(searched.group("rid"))

    async def _parse_live_id(self, rid: str) -> list[MediaContent]:
        await self._ensure_ttwid()
        client = await self.get_client()
        response = await client.get(
            self.LIVE_URL,
            params={
                "aid": "6383",
                "device_platform": "web",
                "browser_language": "zh-CN",
                "browser_platform": "Win32",
                "browser_name": "Chrome",
                "browser_version": "132.0.0.0",
                "web_rid": rid,
            },
            headers=self._get_headers(referer=f"https://live.douyin.com/{rid}"),
        )
        if response.status_code >= 400:
            raise ParseException(f"解析抖音直播失败：HTTP {response.status_code}")
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        rooms = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rooms, list) or not rooms or not isinstance(rooms[0], dict):
            raise ParseException("获取抖音直播间信息失败")
        try:
            room = msgspec.convert(rooms[0], type=Room)
        except (msgspec.ValidationError, ValueError, TypeError) as exc:
            raise ParseException("抖音直播间数据异常") from exc

        cover_url = room.cover.url_list[-1] if room.cover.url_list else None
        author = self.create_author(
            room.owner.nickname or "抖音主播",
            avatar_url=room.owner.avatar_thumb.url_list[0] if room.owner.avatar_thumb.url_list else None,
            uid=room.owner.id_str or None,
        )
        metrics = (
            Metric(label="观看", value=format_num(self._to_int(room.room_view_stats.display_value))),
            Metric(label="点赞", value=format_num(self._to_int(room.like_count))),
        )
        status_text = "直播中" if room.status == 2 else "未开播"
        contents: list[MediaContent] = []
        if cover_url:
            contents = self.create_image_contents([cover_url], headers=self._get_headers(), proxy=self.proxy)
        await self.send_info(
            title=room.title or status_text,
            card_type="common",
            author=author,
            cover_url=cover_url,
            text=status_text,
            url=f"https://live.douyin.com/{rid}",
            metrics=metrics,
        )
        return self.result(contents)

    @handle("aweme_id", r"aweme_id[=:/\s]+(?P<vid>\d{10,})")
    @handle("modal_id", r"modal_id=(?P<vid>\d{10,})")
    @handle("aweme", r"aweme/(?P<vid>\d{10,})")
    @handle("douyin", r"douyin\.com/(?P<ty>video|note|article)/(?P<vid>\d+)")
    @handle("iesdouyin", r"iesdouyin\.com/share/(?P<ty>slides|video|note|article)/(?P<vid>\d+)")
    @handle("m.douyin", r"m\.douyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    @handle("jingxuan.douyin", r"jingxuan\.douyin\.com/m/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    async def _parse_douyin(self, searched: re.Match[str]) -> list[MediaContent]:
        vid = searched.group("vid")
        ty = searched.groupdict().get("ty") or "video"

        aweme = await self._fetch_aweme(vid, ty)
        author = self.create_author(
            name=aweme.author.nickname or "抖音用户",
            avatar_url=aweme.avatar_url,
            description=aweme.author.signature,
            uid=aweme.author_id,
            metrics=aweme.author_metrics,
        )

        comments = await self._fetch_comments(aweme.aweme_id)
        selected = select_video(aweme, self.get_config_value("video_quality", "auto"))

        duration_sec = aweme.video.duration / 1000 if aweme.video and aweme.video.duration > 0 else 0.0
        contents = self._build_contents(aweme, selected, duration_sec)
        if aweme.is_article:
            await self.send_info(
                title=aweme.display_text or "抖音作品",
                card_type="common",
                author=author,
                cover_url=aweme.cover_url,
                text=aweme.body_text or "",
                url=aweme.short_url,
                metrics=aweme.metrics,
                comments=tuple(comments),
                timestamp=aweme.create_time or None,
            )
        elif aweme.images:
            await self.send_info(
                title=aweme.display_text or "抖音作品",
                card_type="common",
                author=author,
                cover_url=aweme.cover_url,
                text=aweme.text or "",
                url=aweme.short_url,
                metrics=aweme.metrics,
                comments=tuple(comments),
                timestamp=aweme.create_time or None,
            )
        else:
            await self.send_info(
                title=aweme.display_text or "抖音作品",
                card_type="video" if aweme.is_video else "common",
                author=author,
                cover_url=aweme.cover_url,
                text=aweme.text or "",
                url=aweme.short_url,
                metrics=aweme.metrics,
                comments=tuple(comments),
                timestamp=aweme.create_time or None,
                video_duration=f"{int(duration_sec)}s" if duration_sec > 0 else None,
                video_quality=selected.quality if selected else None,
            )
        return self.result(contents)

    async def _fetch_aweme(self, aweme_id: str, ty: str) -> Aweme:
        await self._ensure_ttwid()
        try:
            return await self._fetch_detail(aweme_id)
        except ParseException as exc:
            logger.warning(
                f"[Parser·抖音] 详情接口失败，尝试分享页 | aweme_id={aweme_id} | error_type={type(exc).__name__}"
            )
        return await self._fetch_share_page(aweme_id, ty)

    async def _fetch_detail(self, aweme_id: str) -> Aweme:
        client = await self.get_client()
        params = {
            **self.COMMON_PARAMS,
            "aweme_id": aweme_id,
        }
        headers = self._get_headers()
        headers["Origin"] = "https://open.douyin.com"
        headers["Referer"] = "https://open.douyin.com/"
        try:
            response = await client.get(
                self.DETAIL_URL,
                params=params,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ParseException("获取抖音作品详情失败") from exc
        if response.status_code >= 400:
            raise ParseException(f"获取抖音作品详情失败：HTTP {response.status_code}")
        try:
            return decode_aweme(response.content, detail=True)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError, TypeError) as exc:
            raise ParseException("抖音作品详情数据异常") from exc

    async def _fetch_share_page(self, aweme_id: str, ty: str) -> Aweme:
        client = await self.get_client()
        share_url = f"https://www.iesdouyin.com/share/{ty}/{aweme_id}/"
        try:
            response = await client.get(
                share_url,
                headers=self._get_headers(referer="https://www.douyin.com/"),
            )
        except httpx.HTTPError as exc:
            raise ParseException("获取抖音分享页失败") from exc
        if response.status_code >= 400:
            raise ParseException(f"获取抖音分享页失败：HTTP {response.status_code}")
        matched = re.search(
            r"window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>",
            response.text,
            flags=re.DOTALL,
        )
        if matched is None:
            raise ParseException("抖音分享页缺少 _ROUTER_DATA")
        try:
            return decode_aweme(matched.group(1), detail=False)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError, TypeError) as exc:
            raise ParseException("抖音分享页数据异常") from exc

    def _build_contents(self, aweme: Aweme, selected, duration: float) -> list[MediaContent]:
        if aweme.is_video and selected is not None:
            primary_url = selected.url
            fallback_urls = selected.fallback_urls
            return [
                self.create_video_content(
                    primary_url,
                    cover=None,
                    duration=duration,
                    proxy=self.proxy,
                    fallback_urls=fallback_urls,
                )
            ]

        if aweme.images:
            items: list[MediaContent] = []
            headers = self._get_headers()
            for image in aweme.images:
                if image.clip_type in (2, None) and image.url_list:
                    items.extend(self.create_image_contents([image.url_list[0]], headers=headers, proxy=self.proxy))
                    continue
                if image.clip_type == 4 and image.video and image.video.play_addr.urls:
                    items.append(
                        self.create_video_content(
                            image.video.play_addr.urls[0],
                            duration=image.video.duration / 1000,
                            headers=headers,
                            proxy=self.proxy,
                            fallback_urls=image.video.play_addr.urls[1:],
                        )
                    )
                    continue
                if image.clip_type == 5 and image.video and image.video.play_addr.urls and image.url_list:
                    items.append(
                        self.create_live_photo_content(
                            image.video.play_addr.urls[0],
                            image.url_list[0],
                            duration=image.video.duration / 1000,
                            bgm_url=aweme.music_url,
                            headers=headers,
                            proxy=self.proxy,
                            fallback_urls=image.video.play_addr.urls[1:],
                        )
                    )
            return items

        return []

    async def _fetch_comments(self, aweme_id: str) -> list[Comment]:
        if not self.config.get_bool("include_comments", False):
            return []
        limit = comment_limit(self.config.get_int("comment_limit", 5))
        if limit <= 0:
            return []

        await self._ensure_ttwid()
        client = await self.get_client()
        try:
            response = await client.get(
                self.COMMENT_URL,
                params={
                    "device_platform": "webapp",
                    "aid": "6383",
                    "channel": "channel_pc_web",
                    "aweme_id": aweme_id,
                    "cursor": "0",
                    "count": str(limit),
                    "item_type": "0",
                },
                headers=self._get_headers(referer=f"https://www.douyin.com/aweme/{aweme_id}"),
            )
        except httpx.HTTPError as exc:
            logger.warning(f"[Parser·抖音] 评论请求失败 | aweme_id={aweme_id} | error_type={type(exc).__name__}")
            return []
        if response.status_code >= 400:
            logger.warning(f"[Parser·抖音] 评论接口异常 | aweme_id={aweme_id} | status={response.status_code}")
            return []
        try:
            return decode_comments(response.content, limit=limit)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError, TypeError) as exc:
            logger.warning(f"[Parser·抖音] 评论数据异常 | aweme_id={aweme_id} | error_type={type(exc).__name__}")
            return []

    @staticmethod
    def _to_int(value: int | str | None) -> int:
        try:
            return int(value or 0)
        except (ValueError, TypeError):
            return 0
