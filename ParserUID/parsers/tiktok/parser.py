"""TikTok 视频、图文帖与分享短链解析器。"""

from __future__ import annotations

import re
import asyncio
import tempfile
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import msgspec
from msgspec import Struct

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    TikTokInfo,
    TikTokFormat,
    TikTokThumbnail,
    TikTokUniversalData,
    parse_int,
    image_urls,
    select_audio,
    select_video,
    best_thumbnail,
    video_candidates,
    trusted_media_url,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...render.models import Metric
from ...utils.exceptions import ParseException

TIKTOK_HEADERS = {
    "Origin": "https://www.tiktok.com",
    "Referer": "https://www.tiktok.com/",
}
UNIVERSAL_DATA_RE = re.compile(
    r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


class TikTokPayload(Struct):
    _type: str = ""
    id: str = ""
    title: str = ""
    description: str = ""
    uploader: str = ""
    uploader_id: str = ""
    channel: str = ""
    channel_id: str = ""
    author_avatar: str = ""
    duration: float = 0.0
    timestamp: int | None = None
    webpage_url: str = ""
    thumbnail: str = ""
    thumbnails: list[TikTokThumbnail] = []
    images: list[TikTokThumbnail] = []
    formats: list[TikTokFormat] = []
    entries: list[TikTokPayload] = []
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    save_count: int | None = None


class TikTokParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="tiktok", display_name="TikTok")

    def _format_selector(self, quality: str) -> str:
        if quality == "auto":
            return "bv*+ba/b"
        height = quality.removesuffix("p")
        if not height.isdigit():
            return "bv*+ba/b"
        return f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"

    def _author_uid(self, uploader: str, url: str) -> str | None:
        clean_uploader = uploader.strip().lstrip("@")
        if clean_uploader:
            return f"@{clean_uploader}"
        matched = re.search(r"tiktok\.com/@([^/?#\s]+)/", url)
        return f"@{matched.group(1)}" if matched is not None else None

    async def _write_cookie_file(self) -> Path | None:
        """将用户 Cookie 转成 yt-dlp 可读取的 Netscape 临时文件。"""
        raw = self.get_config_value("cookie").strip()
        if not raw:
            return None
        path = Path(tempfile.gettempdir()) / f"parseruid_tiktok_{id(self)}.txt"
        if raw.startswith("# Netscape HTTP Cookie File") or "\t" in raw:
            content = raw if raw.endswith("\n") else f"{raw}\n"
        else:
            lines = ["# Netscape HTTP Cookie File"]
            normalized = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\n", ";")
            for item in normalized.split(";"):
                name, separator, value = item.partition("=")
                if separator and name.strip():
                    lines.append(f".tiktok.com\tTRUE\t/\tFALSE\t0\t{name.strip()}\t{value.strip()}")
            if len(lines) == 1:
                return None
            content = "\n".join(lines) + "\n"
        await asyncio.to_thread(path.write_text, content, "utf-8")
        return path

    async def _extract_info(self, url: str) -> TikTokPayload:
        cookiefile = await self._write_cookie_file()
        try:
            raw = await self.downloader.ytdlp_extract_raw(
                url,
                proxy=self.proxy,
                cookiefile=cookiefile,
                headers=TIKTOK_HEADERS,
                ignore_no_formats_error=True,
            )
        except Exception as exc:
            logger.warning(f"[Parser·TikTok] 元数据提取失败 | error_type={type(exc).__name__}")
            raise ParseException("TikTok 作品信息获取失败") from exc
        finally:
            if cookiefile is not None:
                await asyncio.to_thread(cookiefile.unlink, missing_ok=True)

        try:
            payload = msgspec.convert(raw, type=TikTokPayload)
        except (msgspec.ValidationError, TypeError, ValueError) as exc:
            raise ParseException("TikTok 作品数据格式异常") from exc

        if payload._type == "playlist":
            entries = [
                entry for entry in payload.entries if entry.id or entry.formats or entry.thumbnails or entry.images
            ]
            if len(entries) != 1:
                raise ParseException("TikTok 链接返回了多个作品，无法自动选择")
            payload = entries[0]
        if not payload.id and not payload.formats and not payload.thumbnails:
            raise ParseException("TikTok 作品没有可解析内容")
        return payload

    async def _page_info(self, url: str) -> TikTokInfo | None:
        """公开页补充 yt-dlp 未展开的照片帖图片。"""
        client = await self.get_client()
        try:
            response = await client.get(url, headers=TIKTOK_HEADERS)
        except Exception as exc:
            logger.warning(f"[Parser·TikTok] 公开页请求失败 | error_type={type(exc).__name__}")
            return None
        if response.status_code >= 400:
            return None
        matched = UNIVERSAL_DATA_RE.search(response.text)
        if matched is None:
            return None
        try:
            data = msgspec.json.decode(matched.group(1), type=TikTokUniversalData)
        except (msgspec.DecodeError, msgspec.ValidationError):
            return None
        webapp = data.default_scope.webapp
        detail = webapp.video_detail
        item_info = detail.item_info if detail is not None else None
        item = item_info.item_struct if item_info is not None else None
        if item is None:
            return None

        images: list[TikTokThumbnail] = []
        image_post = item.image_post
        if image_post is not None:
            for image in image_post.images:
                url_value = next(
                    (value for value in image.image_url.url_list if trusted_media_url(value)),
                    "",
                )
                if not url_value:
                    continue
                images.append(
                    TikTokThumbnail(
                        url=url_value,
                        width=parse_int(image.image_width),
                        height=parse_int(image.image_height),
                    )
                )
        covers: list[TikTokThumbnail] = []
        video = item.video
        if video is not None:
            for cover in (video.cover, video.origin_cover, video.dynamic_cover):
                cover_url = next(
                    (value for value in cover.url_list if trusted_media_url(value)),
                    "",
                )
                if cover_url:
                    covers.append(TikTokThumbnail(url=cover_url))
        avatar = next(
            (
                value
                for collection in (
                    item.author.avatar_larger,
                    item.author.avatar_medium,
                    item.author.avatar_thumb,
                )
                for value in collection.url_list
                if trusted_media_url(value)
            ),
            "",
        )
        stats = item.statistics
        author_uid = item.author.unique_id or item.author.id
        return TikTokInfo(
            id=item.id,
            title=image_post.title if image_post is not None else item.desc,
            description=item.desc,
            uploader=item.author.unique_id,
            uploader_id=item.author.id,
            channel=item.author.nickname,
            channel_id=author_uid,
            author_avatar=avatar,
            duration=float(parse_int(video.duration)) if video and video.duration else 0.0,
            timestamp=parse_int(item.create_time) or None,
            webpage_url=url,
            thumbnail=covers[0].url if covers else (images[0].url if images else ""),
            thumbnails=covers,
            images=images,
            formats=[],
            view_count=parse_int(stats.play_count),
            like_count=parse_int(stats.digg_count),
            comment_count=parse_int(stats.comment_count),
            repost_count=parse_int(stats.share_count),
            save_count=parse_int(stats.collect_count),
        )

    @staticmethod
    def _merge_info(base: TikTokPayload, extra: TikTokInfo | None) -> TikTokInfo:
        if extra is None:
            return TikTokInfo(
                id=base.id,
                title=base.title,
                description=base.description,
                uploader=base.uploader,
                uploader_id=base.uploader_id,
                channel=base.channel,
                channel_id=base.channel_id,
                author_avatar=base.author_avatar,
                duration=base.duration,
                timestamp=base.timestamp,
                webpage_url=base.webpage_url,
                thumbnail=base.thumbnail,
                thumbnails=base.thumbnails,
                images=base.images,
                formats=base.formats,
                view_count=base.view_count,
                like_count=base.like_count,
                comment_count=base.comment_count,
                repost_count=base.repost_count,
                save_count=base.save_count,
            )
        return TikTokInfo(
            id=extra.id or base.id,
            title=extra.title or base.title,
            description=extra.description or base.description,
            uploader=extra.uploader or base.uploader,
            uploader_id=extra.uploader_id or base.uploader_id,
            channel=extra.channel or base.channel,
            channel_id=extra.channel_id or base.channel_id,
            author_avatar=extra.author_avatar or base.author_avatar,
            duration=extra.duration or base.duration,
            timestamp=extra.timestamp or base.timestamp,
            webpage_url=extra.webpage_url or base.webpage_url,
            thumbnail=extra.thumbnail or base.thumbnail,
            thumbnails=extra.thumbnails or base.thumbnails,
            images=extra.images or base.images,
            formats=base.formats,
            view_count=extra.view_count if extra.view_count is not None else base.view_count,
            like_count=extra.like_count if extra.like_count is not None else base.like_count,
            comment_count=(extra.comment_count if extra.comment_count is not None else base.comment_count),
            repost_count=(extra.repost_count if extra.repost_count is not None else base.repost_count),
            save_count=extra.save_count if extra.save_count is not None else base.save_count,
        )

    async def _download_video(
        self,
        url: str,
        *,
        fallback_urls: Sequence[str] = (),
    ) -> Path:
        headers = dict(TIKTOK_HEADERS)
        cookie = self.get_config_value("cookie").strip()
        if cookie:
            headers["Cookie"] = cookie
        for candidate_url in (url, *fallback_urls):
            try:
                return await self.downloader.download_video(
                    candidate_url,
                    headers=headers,
                    proxy=self.proxy,
                )
            except (ParseException, OSError, ValueError, RuntimeError):
                continue
        cookiefile = await self._write_cookie_file()
        try:
            return await self.downloader.download_with_ytdlp(
                url,
                proxy=self.proxy,
                cookiefile=cookiefile,
                custom_opts={
                    "http_headers": headers,
                    "format": self._format_selector(self.get_config_value("video_quality", "auto")),
                },
            )
        except Exception as exc:
            logger.warning(f"[Parser·TikTok] 视频下载失败 | error_type={type(exc).__name__}")
            if fallback_urls:
                logger.warning(f"[Parser·TikTok] yt-dlp 备用下载失败 | count={len(fallback_urls)}")
            raise ParseException("TikTok 视频下载失败") from exc
        finally:
            if cookiefile is not None:
                await asyncio.to_thread(cookiefile.unlink, missing_ok=True)

    @handle(
        "tiktok.com",
        r"(?:www|vt|vm|m)\.tiktok\.com/"
        r"(?:@[^/?#\s]+/(?:video|photo)/\d+|t/[A-Za-z0-9_-]+/?|"
        r"share/(?:video|photo)/\d+|live/\d+/?|[A-Za-z0-9_-]+/?)",
    )
    async def _parse(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        url = f"https://{searched.group(0)}"
        payload = await self._extract_info(url)
        public_url = payload.webpage_url or url
        if payload.uploader:
            public_url = f"https://www.tiktok.com/@{payload.uploader}/video/{payload.id}"
        extra = await self._page_info(public_url)
        info = self._merge_info(payload, extra)

        title = info.title or info.description or "TikTok"
        channel = info.channel or info.uploader
        thumbnail = info.thumbnail if trusted_media_url(info.thumbnail) else best_thumbnail(info.thumbnails)
        images = image_urls(info.images) or image_urls(info.thumbnails)
        selected_video = select_video(info.formats, self.get_config_value("video_quality", "auto"))
        video_options = video_candidates(info.formats, self.get_config_value("video_quality", "auto"))
        selected_audio = select_audio(info.formats)

        contents: list[ContentItem] = []
        if video_options:
            primary_video = video_options[0]
            primary_video_url = primary_video.url
            fallback_urls = [item.url for item in video_options[1:]]
            video_content = self.create_video_content(
                None,
                lambda: self._download_video(
                    primary_video_url,
                    fallback_urls=fallback_urls,
                ),
                duration=info.duration,
                headers=TIKTOK_HEADERS,
                proxy=self.proxy,
            )
            video_content.fallback_urls = fallback_urls
            contents.append(video_content)
        elif info.is_photo_post and images:
            contents.extend(
                self.create_image_contents(
                    images,
                    headers=TIKTOK_HEADERS,
                    proxy=self.proxy,
                )
            )
        elif selected_audio is not None:
            audio_url = selected_audio.url

            async def load_audio() -> Path:
                return await self.downloader.download_audio(
                    audio_url,
                    headers=TIKTOK_HEADERS,
                    proxy=self.proxy,
                )

            contents.append(
                self.create_audio_content(
                    loader=load_audio,
                    duration=info.duration,
                )
            )
        if not contents:
            raise ParseException("TikTok 作品没有可发送的媒体")

        author = self.create_author(
            name=channel or "TikTok User",
            avatar_url=info.author_avatar or None,
            uid=self._author_uid(info.uploader, public_url),
        )
        metrics = tuple(
            item
            for item in (
                Metric(label="播放", value=str(info.view_count)) if info.view_count else None,
                Metric(label="点赞", value=str(info.like_count)) if info.like_count else None,
                Metric(label="评论", value=str(info.comment_count)) if info.comment_count else None,
                Metric(label="分享", value=str(info.repost_count)) if info.repost_count else None,
            )
            if item is not None
        )
        await self.send_info(
            title=title,
            card_type="video" if selected_video is not None else "common",
            author=author,
            cover_url=thumbnail,
            text=info.description,
            url=public_url,
            timestamp=info.timestamp,
            metrics=metrics,
            video_duration=(f"{int(info.duration)}s" if selected_video is not None and info.duration > 0 else None),
        )
        return self.result(contents)
