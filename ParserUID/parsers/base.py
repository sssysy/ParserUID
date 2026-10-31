"""ParserUID 平台解析器基类契约与 @handle 装饰器。"""

from __future__ import annotations

import re
from types import FunctionType
from typing import Literal, ClassVar
from pathlib import Path
from contextvars import ContextVar
from collections.abc import Mapping, Callable, Sequence, Awaitable, Coroutine

import httpx

from .config import ParserItemConfig
from ..config import proxy_url, card_image_limit
from ..sender import ContentItem, send_proxy_context
from ..utils.ffmpeg import merge_to_live_mp4
from ..utils.models import (
    Platform,
    PathLoader,
    FileContent,
    TextContent,
    AudioContent,
    ImageContent,
    VideoContent,
    DynamicContent,
    LivePhotoContent,
)
from ..render.models import (
    Author,
    Metric,
    Comment,
    CardBlock,
    PreviewCard,
)
from ..utils.downloader import Downloader, download_image
from ..utils.exceptions import ParseException, RedirectException

DescriptionSink = Callable[[PreviewCard], Awaitable[None]]
HandlerFunc = Callable[[re.Match[str]], Coroutine[object, object, Sequence[ContentItem]]]
AvatarLoader = Callable[[str], Awaitable[Path]]

_CURRENT_DESC_SINK: ContextVar[tuple[DescriptionSink | None, str | None]] = ContextVar(
    "parser_uid_desc_sink",
    default=(None, None),
)
_HANDLER_SPECS_ATTR = "_parser_handler_specs"


def handle(keyword: str, pattern: str) -> Callable[[HandlerFunc], HandlerFunc]:
    """为解析器处理方法注册关键词与正则表达式。"""
    compiled = re.compile(pattern)

    def decorator(func: HandlerFunc) -> HandlerFunc:
        specs = getattr(func, _HANDLER_SPECS_ATTR, None)
        if specs is None:
            specs = []
            setattr(func, _HANDLER_SPECS_ATTR, specs)
        if isinstance(specs, list):
            specs.append((keyword, compiled))
        return func

    return decorator


class BaseParser:
    """所有平台解析器必须继承的基类。"""

    platform: ClassVar[Platform]

    def __init__(self, config: ParserItemConfig | None = None) -> None:
        self.config = (
            config
            if config is not None
            else ParserItemConfig(
                name=self.platform.name,
                enabled=False,
                values={},
            )
        )
        self.mycfg = self.config
        self.cfg = self.config
        self.downloader = Downloader()
        self.headers: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None
        self._client_proxy: str | None = None
        self._curl_client: object | None = None
        self._curl_client_proxy: str | None = None
        self._curl_client_impersonate: str | None = None
        self._handlers: dict[str, tuple[re.Pattern[str], HandlerFunc]] = {}
        self._collect_handlers()

    def get_config_value(self, key: str, default: str = "") -> str:
        """安全读取解析器专属用户配置项字符串。"""
        raw = self.config.values.get(key)
        return str(raw) if raw is not None else default

    @property
    def proxy(self) -> str | None:
        """按解析器开关返回全局代理地址，关闭或未配置时直连。"""
        enabled = self.config.values.get("use_proxy")
        if enabled is not True:
            return None
        return proxy_url()

    @property
    def card_image_limit(self) -> int:
        """预览卡片正文图片数量上限，0 表示不限制。"""
        return card_image_limit()

    def _collect_handlers(self) -> None:
        seen_names: set[str] = set()
        for parser_type in type(self).__mro__:
            for attr_name, attr in vars(parser_type).items():
                if attr_name in seen_names or attr_name.startswith("_BaseParser__"):
                    continue
                seen_names.add(attr_name)
                if not isinstance(attr, FunctionType):
                    continue
                specs = attr.__dict__.get(_HANDLER_SPECS_ATTR)
                if not isinstance(specs, list):
                    continue
                handler = attr.__get__(self, type(self))
                for item in specs:
                    if isinstance(item, tuple) and len(item) == 2:
                        keyword, pattern = item
                        if isinstance(keyword, str) and isinstance(pattern, re.Pattern):
                            self._handlers[keyword] = (pattern, handler)

    @classmethod
    def get_registered_specs(cls) -> list[tuple[str, re.Pattern[str]]]:
        """收集当前类声明的所有关键词与正则规则。"""
        results: list[tuple[str, re.Pattern[str]]] = []
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            if not callable(attr):
                continue
            specs = getattr(attr, _HANDLER_SPECS_ATTR, None)
            if not isinstance(specs, list):
                continue
            for item in specs:
                if isinstance(item, tuple) and len(item) == 2:
                    keyword, pattern = item
                    if isinstance(keyword, str) and isinstance(pattern, re.Pattern):
                        results.append((keyword, pattern))
        return results

    async def get_client(self, proxy: str | None = None) -> httpx.AsyncClient:
        """获取或创建该解析器持有的标准异步 HTTP 会话。"""
        effective_proxy = self.proxy if proxy is None else proxy
        if self._client is not None and not self._client.is_closed and self._client_proxy != effective_proxy:
            await self._client.aclose()
            self._client = None
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                proxy=effective_proxy,
            )
            self._client_proxy = effective_proxy
        return self._client

    async def get_curl_client(self, impersonate: str = "chrome") -> object:
        """获取支持 TLS 指纹伪装的 curl_cffi 异步会话。"""
        effective_proxy = self.proxy
        if self._curl_client is not None and (
            self._curl_client_proxy != effective_proxy or self._curl_client_impersonate != impersonate
        ):
            await self._close_curl_client()
        if self._curl_client is None:
            try:
                from curl_cffi.requests import AsyncSession
            except ImportError as exc:
                raise RuntimeError(f"平台 {self.platform.name} 需要 TLS 指纹伪装，请先安装 curl_cffi") from exc
            self._curl_client = AsyncSession(
                impersonate=impersonate,
                proxy=effective_proxy,
            )
            self._curl_client_proxy = effective_proxy
            self._curl_client_impersonate = impersonate
        return self._curl_client

    async def get_redirect_url(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        """追踪短链单次重定向并返回最终 URL。"""
        client = await self.get_client()
        try:
            response = await client.head(url, headers=headers)
            return str(response.url)
        except Exception as exc:
            raise RedirectException(f"重定向解析失败：{exc}") from exc

    async def get_final_url(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        """追踪完整重定向链条获取最终有效 URL。"""
        client = await self.get_client()
        try:
            response = await client.get(url, headers=headers)
            return str(response.url)
        except Exception as exc:
            raise RedirectException(f"多次重定向解析失败：{exc}") from exc

    async def parse_with_redirect(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Sequence[ContentItem]:
        """自动先追踪重定向再匹配解析。"""
        final_url = await self.get_final_url(url, headers=headers)
        for keyword, (pattern, handler) in self._handlers.items():
            if keyword in final_url:
                matched = pattern.search(final_url)
                if matched is not None:
                    return await handler(matched)
        raise ParseException(f"重定向后的 URL 未能匹配任何已知规则：{final_url}")

    async def download_image(self, url: str) -> Path:
        """复用插件统一缓存下载图片。"""
        return await download_image(url, proxy=self.proxy)

    async def send_info(
        self,
        *,
        title: str,
        card_type: Literal["common", "video"] = "common",
        author: Author | None = None,
        text: str = "",
        url: str | None = None,
        cover: Path | None = None,
        cover_url: str | None = None,
        cover_headers: dict[str, str] | None = None,
        blocks: tuple[CardBlock, ...] = (),
        metrics: tuple[Metric, ...] = (),
        comments: tuple[Comment, ...] = (),
        extra_info: str | None = None,
        timestamp: int | None = None,
        repost: PreviewCard | None = None,
        video_duration: str | None = None,
        video_quality: str | None = None,
    ) -> None:
        """统一入口：构造预览卡片并通过调度上下文的 sink 发送。"""
        sink, proxy = _CURRENT_DESC_SINK.get()
        if sink is None:
            return
        card = PreviewCard(
            platform_name=self.platform.name,
            platform_display_name=self.platform.display_name,
            title=title,
            text=text,
            author=author,
            source_url=url,
            timestamp=str(timestamp) if timestamp is not None else None,
            cover_path=cover,
            cover_url=cover_url,
            cover_headers=cover_headers or {},
            blocks=blocks,
            metrics=metrics,
            comments=comments,
            extra_info=extra_info,
            repost=repost,
            video_duration=video_duration,
            video_quality=video_quality,
            card_type=card_type,
        )
        token = _CURRENT_DESC_SINK.set((sink, proxy))
        send_variable = send_proxy_context()
        send_token = send_variable.set(proxy)
        try:
            await sink(card)
        finally:
            send_variable.reset(send_token)
            _CURRENT_DESC_SINK.reset(token)

    def result(self, contents: Sequence[ContentItem]) -> list[ContentItem]:
        """按顺序返回最终内容队列。"""
        return list(contents)

    def create_text_content(self, text: str) -> TextContent:
        return TextContent(text)

    def create_image_content(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
    ) -> ImageContent:
        return ImageContent(path, loader)

    def create_image_contents(
        self,
        urls: Sequence[str],
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> list[ImageContent]:
        """批量构建延迟下载的图片内容列表。"""
        results: list[ImageContent] = []
        for target_url in urls:
            current_url = target_url

            async def _load_img(url: str = current_url) -> Path:
                return await self.downloader.download_image(url, headers=headers, proxy=proxy)

            results.append(ImageContent(None, _load_img))
        return results

    def create_dynamic_contents(
        self,
        urls: Sequence[str],
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> list[DynamicContent]:
        """批量构建动图/动态内容列表。"""
        results: list[DynamicContent] = []
        for target_url in urls:
            current_url = target_url

            async def _load_dynamic(url: str = current_url) -> Path:
                return await self.downloader.download_file(
                    url,
                    headers=headers,
                    proxy=proxy,
                )

            results.append(DynamicContent(None, _load_dynamic))
        return results

    def create_video_content(
        self,
        url_or_path: str | Path | None,
        loader: PathLoader | None = None,
        *,
        cover: Path | None = None,
        duration: float = 0.0,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        fallback_urls: Sequence[str] | None = None,
    ) -> VideoContent:
        """构建延迟下载的视频内容。"""
        if loader is not None:
            return VideoContent(None, loader, cover=cover, duration=duration)
        if isinstance(url_or_path, Path):
            return VideoContent(url_or_path, cover=cover, duration=duration)
        if url_or_path is None:
            raise ValueError("视频内容必须提供 URL、本地路径或加载器")

        async def _load_video() -> Path:
            return await self.downloader.download_video(
                url_or_path,
                headers=headers,
                proxy=proxy,
                fallback_urls=fallback_urls,
            )

        return VideoContent(None, _load_video, cover=cover, duration=duration)

    def create_live_photo_content(
        self,
        video_url: str,
        image_url: str,
        *,
        cover: Path | None = None,
        duration: float = 0.0,
        bgm_url: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        fallback_urls: Sequence[str] | None = None,
        loop: int = 3,
    ) -> LivePhotoContent:
        """构建实况合成内容，延迟加载时自动调用 ffmpeg 合成。"""

        async def _load_live() -> Path:
            video_path = await self.downloader.download_video(
                video_url,
                headers=headers,
                proxy=proxy,
                fallback_urls=fallback_urls,
            )
            try:
                image_path = await self.downloader.download_image(
                    image_url,
                    headers=headers,
                    proxy=proxy,
                )
                bgm_path = (
                    await self.downloader.download_audio(bgm_url, headers=headers, proxy=proxy)
                    if bgm_url is not None
                    else None
                )
                output = video_path.with_name(f"{video_path.stem}_live.mp4")
                if output.is_file():
                    return output
                return await merge_to_live_mp4(
                    image_path=image_path,
                    video_path=video_path,
                    output_path=output,
                    bgm_path=bgm_path,
                )
            except Exception:
                return video_path

        return LivePhotoContent(None, _load_live, cover=cover, duration=duration, loop=loop)

    def create_audio_content(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
        *,
        duration: float = 0.0,
    ) -> AudioContent:
        return AudioContent(path, loader, duration=duration)

    def create_file_content(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
        *,
        name: str | None = None,
    ) -> FileContent:
        return FileContent(path, loader, name=name)

    def create_author(
        self,
        name: str,
        avatar_url: str | None = None,
        description: str | None = None,
        *,
        uid: str | None = None,
        avatar_headers: Mapping[str, str] | None = None,
        metrics: tuple[Metric, ...] = (),
    ) -> Author:
        """构建作者信息对象。"""
        return Author(
            name=name,
            description=description,
            uid=uid,
            avatar_url=avatar_url,
            avatar_headers=avatar_headers or {},
            metrics=metrics,
        )

    async def parse(
        self,
        keyword: str,
        searched: re.Match[str],
        *,
        desc_sink: DescriptionSink | None = None,
    ) -> Sequence[ContentItem]:
        """入口调度函数，绑定 sink 并调用具体的 @handle 处理器。"""
        if keyword not in self._handlers:
            raise ParseException(f"未注册的关键词：{keyword}")
        _, handler = self._handlers[keyword]
        token = _CURRENT_DESC_SINK.set((desc_sink, self.proxy))
        try:
            return await handler(searched)
        finally:
            _CURRENT_DESC_SINK.reset(token)

    async def close(self) -> None:
        """释放网络会话等长生命周期资源。"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._client_proxy = None
        await self._close_curl_client()
        await self.downloader.close()

    async def _close_curl_client(self) -> None:
        if self._curl_client is not None and hasattr(self._curl_client, "close"):
            close_method = getattr(self._curl_client, "close")
            if callable(close_method):
                close_result = close_method()
                if hasattr(close_result, "__await__"):
                    await close_result
        self._curl_client = None
        self._curl_client_proxy = None
        self._curl_client_impersonate = None

    async def close_session(self) -> None:
        """兼容旧解析器的会话关闭入口。"""
        await self.close()
