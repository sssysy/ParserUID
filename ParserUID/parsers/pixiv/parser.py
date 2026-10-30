"""Pixiv 插画、漫画与小说解析器。"""

from __future__ import annotations

import re
import asyncio
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

import aiofiles

from gsuid_core.logger import logger
from gsuid_core.data_store import get_res_path

from .api import (
    PIXIV_BASE,
    PIXIV_MEDIA_HEADERS,
    PixivAPI,
)
from ..base import BaseParser, handle
from .utils import (
    blur_image,
    build_text,
    images_to_pdf,
    ugoira_to_gif,
    ugoira_to_pdf,
    clean_novel_text,
)
from ...sender import ContentItem
from ...utils.models import Platform, FileContent, ImageContent
from ...render.models import Author
from ...utils.exceptions import ParseException


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value or default))
    except (TypeError, ValueError):
        return default


class PixivParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="pixiv", display_name="Pixiv")

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.api: PixivAPI | None = None

    async def _get_api(self) -> PixivAPI:
        if self.api is None:
            client = await self.get_client()
            cookie = self.get_config_value("cookie")
            self.api = PixivAPI(client, cookie=cookie, retries=2)
        return self.api

    @property
    def media_headers(self) -> dict[str, str]:
        headers = PIXIV_MEDIA_HEADERS.copy()
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _nsfw_action(self, x_restrict: int) -> str | None:
        if x_restrict <= 0:
            return None
        return self.get_config_value("nsfw", "send")

    async def _get_author(self, uid: str) -> Author:
        api = await self._get_api()
        body = await api.get_user_detail(uid)
        avatar_url = str(body.get("imageBig") or body.get("image") or "")
        return self.create_author(
            name=str(body.get("name") or "未知作者"),
            avatar_url=avatar_url or None,
            uid=uid,
        )

    async def _prepare_cover(self, cover_url: str, *, blur: bool) -> Path | None:
        if not cover_url:
            return None
        try:
            cover_path = await self.downloader.download_image(
                cover_url,
                headers=self.media_headers,
                proxy=self.proxy,
            )
            if blur:
                return await asyncio.to_thread(blur_image, cover_path)
            return cover_path
        except Exception as exc:
            logger.warning(f"[Pixiv] 封面下载失败 | error={exc}")
            return None

    async def _page_urls(self, pid: str) -> list[str]:
        api = await self._get_api()
        pages = await api.get_pages(pid)
        urls: list[str] = []
        for page in pages:
            raw_urls = page.get("urls")
            u_dict = raw_urls if isinstance(raw_urls, dict) else {}
            original = str(u_dict.get("original") or "")
            if original:
                urls.append(original)
        return urls

    def _create_pdf_content(
        self,
        pid: str,
        image_urls: list[str],
    ) -> FileContent:
        content = FileContent(name=f"pixiv_{pid}.pdf")

        async def load_pdf() -> Path:
            paths: list[Path] = []
            for u in image_urls:
                try:
                    p = await self.downloader.download_image(
                        u,
                        headers=self.media_headers,
                        proxy=self.proxy,
                    )
                    paths.append(p)
                except Exception:
                    pass
            if not paths:
                raise ParseException("Pixiv 图片下载失败")
            cache_dir = get_res_path("ParserUID") / "cache"
            output_path = cache_dir / f"pixiv_{pid}.pdf"
            return await asyncio.to_thread(images_to_pdf, paths, output_path)

        content.set_loader(load_pdf)
        return content

    async def _build_ugoira(
        self,
        pid: str,
        meta: dict[str, object],
        *,
        as_pdf: bool,
    ) -> Path:
        zip_url = str(meta.get("originalSrc") or meta.get("src") or "")
        raw_frames = meta.get("frames")
        frames = [item for item in raw_frames if isinstance(item, dict)] if isinstance(raw_frames, list) else []
        if not zip_url or not frames:
            raise ParseException("Pixiv 动图元数据不完整")

        cache_dir = get_res_path("ParserUID") / "cache"
        zip_path = await self.downloader.download_file(
            zip_url,
            filename=f"pixiv_ugoira_{pid}.zip",
            headers=self.media_headers,
            proxy=self.proxy,
        )
        if as_pdf:
            output_path = cache_dir / f"pixiv_{pid}.pdf"
            return await asyncio.to_thread(
                ugoira_to_pdf,
                zip_path,
                frames,
                output_path,
            )
        output_path = cache_dir / f"pixiv_{pid}.gif"
        return await asyncio.to_thread(
            ugoira_to_gif,
            zip_path,
            frames,
            output_path,
        )

    async def _get_series_info(self, body: dict[str, object], user_id: str) -> str:
        series_nav = body.get("seriesNavData")
        if not isinstance(series_nav, dict):
            return ""
        series_id = str(series_nav.get("seriesId") or "")
        order = _to_int(series_nav.get("order"))
        series_title = str(series_nav.get("title") or "")
        series_type = str(series_nav.get("seriesType") or "manga")
        series_url = f"{PIXIV_BASE}/user/{user_id}/series/{series_id}" if series_id and user_id else ""
        label = "小说系列" if series_type == "novel" else "漫画系列"

        total: int | None = None
        api = await self._get_api()
        if series_id:
            if series_type == "novel":
                total = await api.get_novel_series_total(series_id)
            else:
                total = await api.get_manga_series_total(series_id, user_id)
        if total is not None:
            return f"总共 {total} 部作品中的第 {order} 部\n{label}: {series_url}"
        return f"系列: {series_title}\n当前收录第 {order} 部\n{label}: {series_url}"

    @handle("pixiv.net/artworks", r"pixiv\.net/artworks/(?P<pid>\d+)")
    async def _handle_artworks(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        pid = searched.groupdict().get("pid") or ""
        api = await self._get_api()
        body = await api.get_illust_detail(pid)
        if _to_int(body.get("illustType")) == 1:
            return await self._handle_manga(pid, body)
        return await self._handle_illust(pid, body)

    @handle("pixivid", r"(?<![a-zA-Z])pixivid\s*(?P<pid>\d+)")
    @handle("pid", r"(?<![a-zA-Z])pid\s*(?P<pid>\d+)")
    async def _handle_pid(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        pid = searched.groupdict().get("pid") or ""
        api = await self._get_api()
        body = await api.get_illust_detail_optional(pid)
        if body is not None:
            if _to_int(body.get("illustType")) == 1:
                return await self._handle_manga(pid, body)
            return await self._handle_illust(pid, body)
        novel = await api.get_novel_detail_optional(pid)
        if novel is None:
            raise ParseException(f"PID {pid} 不是有效的插画、漫画或小说 ID")
        return await self._handle_novel_core(pid, novel)

    @handle("pixiv.net/novel/show", r"pixiv\.net/novel/show\.php\?id=(?P<nid>\d+)")
    @handle("pixiv.net/novel/", r"pixiv\.net/novel/(?P<nid>\d+)")
    async def _handle_novel(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        nid = searched.groupdict().get("nid") or ""
        api = await self._get_api()
        body = await api.get_novel_detail(nid)
        return await self._handle_novel_core(nid, body)

    async def _handle_novel_core(
        self,
        nid: str,
        body: dict[str, object],
    ) -> Sequence[ContentItem]:
        action = self._nsfw_action(_to_int(body.get("xRestrict")))
        if action == "ignore":
            await self.send_info(
                title="Pixiv 小说",
                card_type="common",
                extra_info="R18 内容已忽略",
            )
            return self.result([])

        user_id = str(body.get("userId") or "")
        author = await self._get_author(user_id) if user_id else None
        title = str(body.get("title") or "")
        text = build_text(body)
        cover = await self._prepare_cover(
            str(body.get("coverUrl") or ""),
            blur=action == "blur",
        )
        novel_text = clean_novel_text(str(body.get("content") or ""))
        if not novel_text:
            raise ParseException("Pixiv 小说内容为空")

        file_name = f"pixiv_{nid}.txt"
        content = FileContent(name=file_name)

        async def load_novel() -> Path:
            cache_dir = get_res_path("ParserUID") / "cache"
            output_path = cache_dir / file_name
            async with aiofiles.open(output_path, "w", encoding="utf-8") as file:
                await file.write(novel_text)
            return output_path

        content.set_loader(load_novel)
        character_count = _to_int(body.get("characterCount"))
        extra_info = f"字数: {character_count}" if character_count > 0 else None
        await self.send_info(
            title=title or "Pixiv 小说",
            card_type="common",
            author=author,
            cover=cover,
            text=text,
            url=f"{PIXIV_BASE}/novel/show.php?id={nid}",
            extra_info=extra_info,
        )
        return self.result([content])

    async def _handle_illust(
        self,
        pid: str,
        body: dict[str, object],
    ) -> Sequence[ContentItem]:
        action = self._nsfw_action(_to_int(body.get("xRestrict")))
        if action == "ignore":
            await self.send_info(
                title="Pixiv 插画",
                card_type="common",
                extra_info="R18 内容已忽略",
            )
            return self.result([])

        user_id = str(body.get("userId") or "")
        author = await self._get_author(user_id) if user_id else None
        title = str(body.get("title") or "")
        text = build_text(body)
        page_count = max(_to_int(body.get("pageCount"), 1), 1)
        original_type = _to_int(body.get("illustType"))
        illust_type = original_type
        raw_urls = body.get("urls")
        u_dict = raw_urls if isinstance(raw_urls, dict) else {}
        cover_url = str(u_dict.get("regular") or "")
        cover = await self._prepare_cover(cover_url, blur=action == "blur")

        api = await self._get_api()
        ugoira_meta: dict[str, object] | None = None
        ugoira_dict: dict[str, object] = {}
        if illust_type == 2:
            ugoira_meta = await api.get_ugoira_meta(pid)
            if ugoira_meta is not None:
                ugoira_dict = ugoira_meta
            if ugoira_meta is None:
                illust_type = 0

        contents: list[ContentItem] = []
        if action == "blur":
            if illust_type == 2 and ugoira_meta is not None:
                pdf_content = FileContent(name=f"pixiv_{pid}.pdf")

                async def load_ugoira_pdf():
                    return await self._build_ugoira(
                        pid,
                        ugoira_dict,
                        as_pdf=True,
                    )

                pdf_content.set_loader(load_ugoira_pdf)
                contents.append(pdf_content)
            else:
                image_urls = await self._page_urls(pid)
                if not image_urls:
                    raise ParseException("Pixiv 未找到插画图片")
                contents.append(self._create_pdf_content(pid, image_urls))
        elif illust_type == 2 and ugoira_meta is not None:
            gif = ImageContent()

            async def load_gif():
                return await self._build_ugoira(
                    pid,
                    ugoira_dict,
                    as_pdf=False,
                )

            gif.set_loader(load_gif)
            contents.append(gif)
        else:
            image_urls = await self._page_urls(pid)
            if not image_urls:
                raise ParseException("Pixiv 未找到插画图片")
            contents.extend(
                self.create_image_contents(
                    image_urls,
                    headers=self.media_headers,
                    proxy=self.proxy,
                )
            )

        info_parts: list[str] = []
        if page_count > 1:
            info_parts.append(f"共 {page_count} P")
        if original_type == 2 and ugoira_meta is None:
            info_parts.append("动图元数据获取失败，已回退为静态图片")
        await self.send_info(
            title=title or "Pixiv 插画",
            card_type="common",
            author=author,
            cover=cover,
            text=text,
            url=f"{PIXIV_BASE}/artworks/{pid}",
            extra_info="\n".join(info_parts) if info_parts else None,
        )
        return self.result(contents)

    async def _handle_manga(
        self,
        pid: str,
        body: dict[str, object],
    ) -> Sequence[ContentItem]:
        action = self._nsfw_action(_to_int(body.get("xRestrict")))
        if action == "ignore":
            await self.send_info(
                title="Pixiv 漫画",
                card_type="common",
                extra_info="R18 内容已忽略",
            )
            return self.result([])

        user_id = str(body.get("userId") or "")
        author = await self._get_author(user_id) if user_id else None
        title = str(body.get("title") or "")
        text = build_text(body)
        page_count = max(_to_int(body.get("pageCount"), 1), 1)
        raw_urls = body.get("urls")
        u_dict = raw_urls if isinstance(raw_urls, dict) else {}
        cover_url = str(u_dict.get("regular") or "")
        series_info = await self._get_series_info(body, user_id)
        cover = await self._prepare_cover(cover_url, blur=action == "blur")

        max_page = _to_int(self.get_config_value("max_page", "30"))
        if max_page > 0 and page_count > max_page:
            info = (
                f"{series_info}\n" if series_info else ""
            ) + f"漫画共 {page_count} 页，超过单次限制 {max_page}，仅发送封面。"
            await self.send_info(
                title=title or "Pixiv 漫画",
                card_type="common",
                author=author,
                cover=cover,
                text=text,
                url=f"{PIXIV_BASE}/artworks/{pid}",
                extra_info=info,
            )
            return self.result([ImageContent(cover)] if cover else [])

        image_urls = await self._page_urls(pid)
        if not image_urls:
            raise ParseException("Pixiv 未找到漫画图片")
        pdf = self._create_pdf_content(pid, image_urls)
        await self.send_info(
            title=title or "Pixiv 漫画",
            card_type="common",
            author=author,
            cover=cover,
            text=text,
            url=f"{PIXIV_BASE}/artworks/{pid}",
            extra_info=series_info or None,
        )
        return self.result([pdf])
