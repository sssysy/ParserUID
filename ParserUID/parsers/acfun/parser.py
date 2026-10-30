"""AcFun 链接解析器实现。"""

from __future__ import annotations

import re
from typing import ClassVar
from pathlib import Path

import msgspec

from ..base import BaseParser, handle
from .video import play_urls, quality_label, select_play_info
from .models import AcfunVideoInfo
from ...utils.models import Platform, MediaContent
from ...utils.exceptions import ParseException


class AcfunParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="acfun", display_name="AcFun")

    INFO_URL: ClassVar[str] = "https://api-new.app.acfun.cn/rest/app/douga/info"
    MKEY: ClassVar[str] = (
        "AAHewK3eIAAyMjA2MDMyMjQAAhAAMEP1uwSG3TvhYAAAAO5fOOpIdKsH2h4IGsF6"
        "BlVwnGQA6_eLEvGiajzUp4_YthxOPC-hxcOpTk0SPSrxyhbdkmIwsXnF9PgS5ly8eQ"
        "yjuXlcS7VpWG0QlK0HakVDamteMHNHIui0A8V4tmELqQ=="
    )

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Referer": "https://www.acfun.cn/",
            "Origin": "https://www.acfun.cn",
        }
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    async def _fetch_video_info(self, acid: str) -> AcfunVideoInfo:
        client = await self.get_client()
        try:
            response = await client.get(
                self.INFO_URL,
                params={"mkey": self.MKEY, "dougaId": acid},
                headers=self._get_headers(),
            )
            if response.status_code >= 400:
                raise ParseException(f"AcFun 视频信息请求失败：HTTP {response.status_code}")
            payload = response.content
        except Exception as exc:
            raise ParseException(f"AcFun 视频信息请求失败：{exc}") from exc

        try:
            info = msgspec.json.decode(payload, type=AcfunVideoInfo)
        except Exception as exc:
            raise ParseException("AcFun 视频信息格式解析失败") from exc

        if not info.title and not info.current_video_info.play_infos:
            raise ParseException("AcFun 视频信息为空")
        return info

    async def _download_video(self, url: str, fallback_urls: list[str], acid: str) -> Path:
        headers = self._get_headers()
        return await self.downloader.download_m3u8_video(
            url,
            filename=f"acfun_{acid}.mp4",
            headers=headers,
            proxy=self.proxy,
            fallback_urls=fallback_urls,
        )

    @handle(
        "acfun.cn/v",
        r"(?<![A-Za-z0-9.-])(?:https?://)?(?:www\.|m\.)?acfun\.cn/v/"
        r"(?:\?ac=|ac)(?P<acid>\d+)"
        r"(?:[?&][^\s#]*)?",
    )
    async def _parse(self, searched: re.Match[str]) -> list[MediaContent]:
        acid = searched.group("acid")
        page_url = f"https://www.acfun.cn/v/ac{acid}"
        info = await self._fetch_video_info(acid)

        quality_pref = self.get_config_value("video_quality", "auto")
        selected = select_play_info(
            info.current_video_info.play_infos,
            quality_pref,
        )
        if selected is None:
            raise ParseException("AcFun 视频没有可用的播放地址")
        urls = play_urls(selected)
        if not urls:
            raise ParseException("AcFun 视频播放地址无效")

        author = self.create_author(
            name=info.user.name or "未知用户",
            avatar_url=info.user.head_url or None,
            uid=info.user.user_id or None,
        )

        await self.send_info(
            title=info.title,
            card_type="video",
            author=author,
            cover_url=info.cover_url or None,
            text=info.text,
            url=page_url,
            timestamp=info.timestamp,
            metrics=info.metrics,
            video_duration=f"{int(info.duration)}s" if info.duration > 0 else None,
            video_quality=quality_label(selected),
        )

        primary_url = urls[0]
        backup_urls = urls[1:]
        content = self.create_video_content(
            None,
            lambda: self._download_video(primary_url, backup_urls, acid),
            duration=info.duration,
            proxy=self.proxy,
        )
        return self.result([content])
