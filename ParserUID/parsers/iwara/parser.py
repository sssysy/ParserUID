"""Iwara 链接解析器。"""

from __future__ import annotations

import re
import asyncio
import hashlib
from typing import ClassVar
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from collections.abc import Sequence

import msgspec
from PIL import Image, ImageFilter

from ..base import BaseParser, handle
from .models import (
    IMAGE_HOST,
    IwaraImageRaw,
    IwaraVideoRaw,
    VideoFilesResponse,
    avatar_url,
    image_urls,
    is_iwara_url,
    choose_quality_url,
    image_thumbnail_url,
    video_thumbnail_url,
)
from .request import API_HOST, IwaraClient
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException

IWARA_SALT = "_mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN"
IMAGE_HOSTS = frozenset({IMAGE_HOST})


class IwaraParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="iwara", display_name="Iwara")

    def _get_client(self) -> IwaraClient:
        headers = {
            "Accept": "application/json",
            "Origin": "https://www.iwara.tv",
            "Referer": "https://www.iwara.tv/",
        }
        cookie = self.get_config_value("cookie")
        return IwaraClient(cookie=cookie, headers=headers, proxy=self.proxy)

    @handle(
        "iwara.tv/video",
        r"(?<![A-Za-z0-9.-])(?:www\.)?iwara\.tv/video/(?P<video_id>[A-Za-z0-9]+)",
    )
    async def _parse_video(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        video_id = searched.group("video_id")
        client = self._get_client()
        try:
            raw_bytes = await client.fetch_api(f"https://{API_HOST}/video/{video_id}")
            video = msgspec.json.decode(raw_bytes, type=IwaraVideoRaw)
        finally:
            await client.close()

        if self._should_ignore(video.rating):
            await self.send_info(
                title=video.title,
                card_type="common",
                extra_info="⚠ 该视频为 R18 内容，已按配置忽略",
                url=f"https://www.iwara.tv/video/{video.id}",
            )
            return ()

        quality = self.get_config_value("video_quality", "Source")
        download_url, selected_quality = await self._resolve_video_url(video.fileUrl, quality)
        thumb = video_thumbnail_url(video.file, video.thumbnail)
        cover_path = await self._get_cover(thumb, video.rating)

        author_name = f"{video.user.name} ({video.user.username})" if video.user.username else video.user.name
        author = self.create_author(author_name, avatar_url(video.user))
        duration = max(0.0, float(video.file.duration))
        desc_text = self._description_text(video.body, tuple(t.id for t in video.tags), "视频")

        await self.send_info(
            title=video.title,
            card_type="video",
            author=author,
            cover=cover_path,
            video_duration=_format_duration(duration),
            video_quality=selected_quality,
            text=desc_text,
            url=f"https://www.iwara.tv/video/{video.id}",
        )

        video_item = self.create_video_content(
            download_url,
            duration=duration,
            headers={"Referer": "https://www.iwara.tv/"},
            proxy=self.proxy,
        )
        return self.result([video_item])

    @handle(
        "iwara.tv/image",
        r"(?<![A-Za-z0-9.-])(?:www\.)?iwara\.tv/image/(?P<image_id>[A-Za-z0-9]+)",
    )
    async def _parse_image(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        image_id = searched.group("image_id")
        client = self._get_client()
        try:
            raw_bytes = await client.fetch_api(f"https://{API_HOST}/image/{image_id}")
            image_data = msgspec.json.decode(raw_bytes, type=IwaraImageRaw)
        finally:
            await client.close()

        if self._should_ignore(image_data.rating):
            await self.send_info(
                title=image_data.title,
                card_type="common",
                extra_info="⚠ 该图片为 R18 内容，已按配置忽略",
                url=f"https://www.iwara.tv/image/{image_data.id}",
            )
            return ()

        nsfw_mode = self.get_config_value("nsfw", "blur")
        blurred = image_data.rating == "ecchi" and nsfw_mode == "blur"
        urls = [image_urls(f, large=blurred) for f in image_data.files]
        if not urls:
            raise ParseException("Iwara 图片列表为空")

        thumb = image_thumbnail_url(image_data.thumbnail)
        cover_path = await self._get_cover(thumb, image_data.rating)

        author_name = (
            f"{image_data.user.name} ({image_data.user.username})" if image_data.user.username else image_data.user.name
        )
        author = self.create_author(author_name, avatar_url(image_data.user))
        desc_text = self._description_text(image_data.body, tuple(t.id for t in image_data.tags), "图片")

        await self.send_info(
            title=image_data.title,
            card_type="common",
            author=author,
            cover=cover_path,
            text=desc_text,
            url=f"https://www.iwara.tv/image/{image_data.id}",
        )

        items = self._build_image_contents(urls, blur=blurred)
        return self.result(items)

    async def _resolve_video_url(self, file_url: str, quality: str) -> tuple[str, str]:
        if not is_iwara_url(file_url):
            raise ParseException("Iwara 视频文件地址来源异常")

        parsed = urlparse(file_url)
        file_id = parsed.path.rstrip("/").split("/")[-1]
        expires_list = parse_qs(parsed.query).get("expires", [""])
        expires = expires_list[0] if expires_list else ""
        if not file_id or not expires:
            raise ParseException("Iwara 视频文件地址格式异常")

        x_version = hashlib.sha1(f"{file_id}_{expires}{IWARA_SALT}".encode("utf-8")).hexdigest()
        client = self._get_client()
        try:
            raw_files = await client.fetch_video_files(file_url, x_version=x_version)
            files_resp = msgspec.json.decode(raw_files, type=VideoFilesResponse)
        finally:
            await client.close()

        download_url, selected = choose_quality_url(files_resp.results, quality)
        if not is_iwara_url(download_url):
            raise ParseException("Iwara 视频下载地址来源异常")
        return download_url, selected

    def _should_ignore(self, rating: str) -> bool:
        return rating == "ecchi" and self.get_config_value("nsfw", "blur") == "ignore"

    async def _get_cover(self, url: str | None, rating: str) -> Path | None:
        if not url:
            return None
        nsfw_mode = self.get_config_value("nsfw", "blur")
        cover_path = await self.downloader.download_image(
            url,
            headers={"Referer": "https://www.iwara.tv/"},
            proxy=self.proxy,
        )
        if rating == "ecchi" and nsfw_mode == "blur":
            return await asyncio.to_thread(self._blur, cover_path)
        return cover_path

    def _build_image_contents(self, urls: Sequence[str], *, blur: bool) -> list[ContentItem]:
        items: list[ContentItem] = []
        for url in urls:
            current_url = url

            async def load_img(img_url: str = current_url, should_blur: bool = blur) -> Path:
                path = await self.downloader.download_image(
                    img_url,
                    headers={"Referer": "https://www.iwara.tv/"},
                    proxy=self.proxy,
                )
                if should_blur:
                    return await asyncio.to_thread(self._blur, path)
                return path

            items.append(self.create_image_content(loader=load_img))
        return items

    @staticmethod
    def _blur(image_path: Path, radius: int = 20) -> Path:
        output_path = image_path.parent / f"{image_path.stem}_blur{image_path.suffix}"
        if output_path.exists():
            return output_path
        with Image.open(image_path) as image:
            image.filter(ImageFilter.GaussianBlur(radius=radius)).save(output_path)
        return output_path

    @staticmethod
    def _description_text(
        body: str | None,
        tags: Sequence[str],
        kind: str,
    ) -> str:
        parts: list[str] = []
        if body:
            parts.append(f"{kind}描述: {body}")
        if tags:
            parts.append("TAG: " + ", ".join(f"#{t}" for t in tags))
        return "\n\n".join(parts)


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"
