"""跨平台通用文件与多媒体流式下载工具。"""

from __future__ import annotations

import io
import os
import time
import uuid
import asyncio
import hashlib
from typing import Final
from pathlib import Path
from urllib.parse import urlparse
from collections.abc import Sequence

import httpx
from PIL import Image, UnidentifiedImageError

from gsuid_core.logger import logger
from gsuid_core.data_store import get_res_path

from .exceptions import DownloadException

DEFAULT_MAX_BYTES: Final = 500 * 1024 * 1024
DEFAULT_IMAGE_MAX_BYTES: Final = 20 * 1024 * 1024
DEFAULT_CACHE_MAX_AGE_SECONDS: Final = 7 * 24 * 60 * 60
CACHE_DIR: Final = get_res_path("ParserUID") / "cache"

_CONNECT_TIMEOUT: Final = 15.0
_READ_TIMEOUT: Final = 60.0
_CHUNK_SIZE: Final = 64 * 1024
_USER_AGENT: Final = "ParserUID/1.0 (+https://github.com/Genshin-bots/gsuid_core)"

_IMAGE_SIGNATURES: Final = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"RIFF",
    b"BM",
)
_PIL_FORMATS: Final = frozenset({"PNG", "JPEG", "GIF", "WEBP", "BMP"})


class Downloader:
    """流式网络下载器，支持断点、重试与 yt-dlp。"""

    def __init__(self, cache_dir: Path = CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass
        self._client: httpx.AsyncClient | None = None

    async def get_client(self, proxy: str | None = None) -> httpx.AsyncClient:
        """惰性创建或复用 HTTP 客户端。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
                follow_redirects=True,
                proxy=proxy,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._client

    async def close(self) -> None:
        """释放持有的网络会话。"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _url_to_filename(self, url: str, default_suffix: str = "") -> str:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        suffix = Path(urlparse(url).path).suffix.lower()
        if not suffix:
            suffix = (
                default_suffix if default_suffix.startswith(".") else f".{default_suffix}" if default_suffix else ".bin"
            )
        return f"{digest}{suffix}"

    async def download_file(
        self,
        url: str,
        *,
        filename: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        fallback_urls: Sequence[str] | None = None,
    ) -> Path:
        """流式分块下载文件到插件缓存目录。"""
        actual_name = filename if filename is not None else self._url_to_filename(url)
        target = self.cache_dir / actual_name

        if await asyncio.to_thread(target.is_file) and (await asyncio.to_thread(lambda: target.stat().st_size)) > 0:
            return target

        candidates = [url, *(fallback_urls or ())]
        last_error: Exception | None = None

        for candidate_url in candidates:
            try:
                return await self._stream_download(candidate_url, target, headers, proxy, max_bytes)
            except Exception as exc:
                last_error = exc
                logger.warning(f"[下载] 链接下载失败，尝试备用地址：{candidate_url} | {exc}")

        raise DownloadException(f"文件下载失败（尝试了 {len(candidates)} 个地址）：{last_error}")

    async def _stream_download(
        self,
        url: str,
        target: Path,
        headers: dict[str, str] | None,
        proxy: str | None,
        max_bytes: int,
    ) -> Path:
        client = await self.get_client(proxy=proxy)
        req_headers = {"User-Agent": _USER_AGENT, **(headers or {})}
        temporary = self.cache_dir / f".{target.name}.{uuid.uuid4().hex}.part"

        try:
            async with client.stream("GET", url, headers=req_headers) as response:
                if response.status_code >= 400:
                    raise DownloadException(f"HTTP 错误 {response.status_code}")

                total_downloaded = 0
                with temporary.open("wb") as handle:
                    async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                        total_downloaded += len(chunk)
                        if total_downloaded > max_bytes:
                            raise DownloadException(f"文件大小超出限制（上限 {max_bytes} 字节）")
                        handle.write(chunk)

            if total_downloaded == 0:
                raise DownloadException("下载得到 0 字节文件")

            await asyncio.to_thread(os.replace, temporary, target)
            return target
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    async def download_image(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        max_bytes: int = DEFAULT_IMAGE_MAX_BYTES,
    ) -> Path:
        """下载图片并校验格式合法性。"""
        target = await self.download_file(
            url,
            headers=headers,
            proxy=proxy,
            max_bytes=max_bytes,
        )
        await asyncio.to_thread(self._verify_image, target)
        return target

    async def download_video(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        fallback_urls: Sequence[str] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> Path:
        """下载视频流文件。"""
        filename = self._url_to_filename(url, default_suffix=".mp4")
        return await self.download_file(
            url,
            filename=filename,
            headers=headers,
            proxy=proxy,
            fallback_urls=fallback_urls,
            max_bytes=max_bytes,
        )

    async def download_audio(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> Path:
        """下载音频流文件。"""
        filename = self._url_to_filename(url, default_suffix=".mp3")
        return await self.download_file(
            url,
            filename=filename,
            headers=headers,
            proxy=proxy,
            max_bytes=max_bytes,
        )

    async def download_with_ytdlp(
        self,
        url: str,
        *,
        proxy: str | None = None,
        custom_opts: dict[str, object] | None = None,
    ) -> Path:
        """使用 yt-dlp 后台线程提取并下载多媒体。"""
        try:
            import yt_dlp
        except ImportError as exc:
            raise DownloadException("未安装 yt-dlp，无法解析此类视频") from exc

        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        outtmpl = str(self.cache_dir / f"{digest}.%(ext)s")

        def _sync_ytdlp_run() -> Path:
            opts: dict[str, object] = {
                "outtmpl": outtmpl,
                "format": "bestvideo+bestaudio/best",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
            if proxy is not None:
                opts["proxy"] = proxy
            if custom_opts is not None:
                opts.update(custom_opts)

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)
                result_path = Path(downloaded_file)
                if not result_path.is_file():
                    mp4_alt = result_path.with_suffix(".mp4")
                    if mp4_alt.is_file():
                        return mp4_alt
                    mkv_alt = result_path.with_suffix(".mkv")
                    if mkv_alt.is_file():
                        return mkv_alt
                return result_path

        return await asyncio.to_thread(_sync_ytdlp_run)

    @staticmethod
    def _verify_image(path: Path) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise DownloadException("图片文件不存在或为空")
        data = path.read_bytes()
        if not any(data.startswith(sig) for sig in _IMAGE_SIGNATURES):
            raise DownloadException("文件签名不是有效图片")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise DownloadException("无法识别的图片文件") from exc

    async def clean_cache(self, max_age_seconds: int = DEFAULT_CACHE_MAX_AGE_SECONDS) -> int:
        """清理超过指定时长的缓存文件。"""
        if not await asyncio.to_thread(self.cache_dir.is_dir):
            return 0
        cutoff = time.time() - max(0, max_age_seconds)
        entries = await asyncio.to_thread(list, self.cache_dir.iterdir())
        removed = 0
        for entry in entries:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
        return removed


_GLOBAL_DOWNLOADER = Downloader()


async def download_image(url: str, *, max_bytes: int = DEFAULT_IMAGE_MAX_BYTES) -> Path:
    """兼容旧调用接口的图片下载辅助函数。"""
    return await _GLOBAL_DOWNLOADER.download_image(url, max_bytes=max_bytes)


async def clean_image_cache(*, max_age_seconds: int = DEFAULT_CACHE_MAX_AGE_SECONDS) -> int:
    """兼容旧调用接口的图片缓存清理函数。"""
    return await _GLOBAL_DOWNLOADER.clean_cache(max_age_seconds=max_age_seconds)
