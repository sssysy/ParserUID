"""磁力链接解析器。"""

from __future__ import annotations

import re
import asyncio
from typing import ClassVar
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Sequence

import httpx
import msgspec
from PIL import Image, ImageFilter

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    MagnetInfoResponse,
)
from ...sender import ContentItem
from ...utils.models import Platform, ImageContent
from ...utils.exceptions import ParseException

API_URL = "https://whatslink.info/api/v1/link"
SCREENSHOT_HOST = "whatslink.info"
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def format_size(size_bytes: float | int) -> str:
    size = float(size_bytes)
    if size <= 0:
        return "未知"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def extract_screenshot_urls(response: MagnetInfoResponse) -> list[str]:
    urls: list[str] = []
    for item in response.screenshots:
        url = item.screenshot.strip()
        if not url.startswith("https://"):
            continue
        parsed = urlsplit(url)
        if parsed.hostname != SCREENSHOT_HOST:
            continue
        if url not in urls:
            urls.append(url)
    return urls


class MagnetParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name="magnet",
        display_name="磁力链接",
    )

    async def _fetch_info(self, magnet: str) -> MagnetInfoResponse:
        client = await self.get_client()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.get(API_URL, params={"url": magnet})
                if resp.status_code in RETRYABLE_STATUSES:
                    last_error = httpx.HTTPStatusError(
                        f"状态码 {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                    if attempt < 2:
                        await asyncio.sleep(1 + attempt)
                        continue
                    raise ParseException("磁力解析服务暂时不可用，请稍后重试") from last_error
                if resp.status_code >= 400:
                    raise ParseException(f"磁力解析失败: HTTP {resp.status_code}")
                data = msgspec.json.decode(resp.content, type=MagnetInfoResponse)
                if data.error.strip():
                    raise ParseException("磁力链接无法识别或资源不存在")
                return data
            except ParseException:
                raise
            except (httpx.HTTPError, msgspec.DecodeError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise ParseException("磁力解析请求失败，请稍后重试") from exc
        raise ParseException("磁力解析请求失败，请稍后重试") from last_error

    @staticmethod
    def _blur(image_path: Path, radius: int = 15) -> Path:
        output_path = image_path.with_name(f"{image_path.stem}_blur{image_path.suffix or '.jpg'}")
        with Image.open(image_path) as image:
            blurred = image.filter(ImageFilter.GaussianBlur(radius=radius))
            blurred.save(output_path)
        return output_path

    def _image_contents(
        self,
        image_urls: list[str],
        *,
        blur: bool,
    ) -> list[ImageContent]:
        contents: list[ImageContent] = []
        headers = {"Referer": API_URL}

        for image_url in image_urls:
            current_url = image_url

            async def load_image(
                url: str = current_url,
                should_blur: bool = blur,
            ) -> Path:
                image_path = await self.downloader.download_image(
                    url,
                    headers=headers,
                )
                if not should_blur:
                    return image_path
                try:
                    return await asyncio.to_thread(self._blur, image_path)
                except (OSError, ValueError) as exc:
                    logger.warning(f"[磁力] 预览图模糊失败：{exc}")
                    return image_path

            contents.append(self.create_image_content(loader=load_image))
        return contents

    @handle("magnet", r"""magnet:\?(?P<query>[^\s<>"']+)""")
    async def _parse_magnet(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        magnet = searched.group(0)
        data = await self._fetch_info(magnet)

        title = data.name or "未知资源"
        file_type = data.file_type or "未知"
        size = format_size(data.size)
        image_urls = extract_screenshot_urls(data)
        mode = self.get_config_value("image_send_mode", "original")

        text = f"标题：{title}\n\n磁链类型：{file_type}\n\n文件大小：{size}"
        await self.send_info(
            title=title,
            card_type="common",
            text=text,
            url=magnet,
            extra_info=f"预览图：{len(image_urls)} 张",
        )

        items: list[ContentItem] = [self.create_text_content(text)]
        if image_urls and mode in {"blur", "original"}:
            items.extend(self._image_contents(image_urls, blur=mode == "blur"))
        return self.result(items)
