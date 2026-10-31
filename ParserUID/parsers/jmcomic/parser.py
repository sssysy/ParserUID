"""jmcomic 漫画链接解析器。"""

from __future__ import annotations

import re
import asyncio
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

from PIL import Image, ImageFilter

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import JmPhotoItem, JmPhotoDetail, JmAsyncClientProtocol
from .comment import comment_limit, decode_comments
from ...sender import ContentItem
from ...utils.models import Platform
from ...render.models import Comment
from ...utils.exceptions import ParseException

try:
    import jmcomic
    from jmcomic import (
        JmOption,
        JmcomicText,
        JmImageTool,
        JmcomicException,
        MissingAlbumPhotoException,
    )
except ImportError:
    jmcomic = None
    JmOption = None
    JmImageTool = None
    JmcomicText = None
    JmcomicException = Exception
    MissingAlbumPhotoException = Exception


def _option_client(option: object) -> dict[str, object]:
    """取得 jmcomic 选项中的 HTTP 客户端配置字典。"""
    if not hasattr(option, "client"):
        raise ParseException("当前 jmcomic 选项结构不支持代理配置")
    client = option.client
    if not isinstance(client, dict):
        raise ParseException("当前 jmcomic 选项结构不支持代理配置")
    return client


class JMComicParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="jmcomic", display_name="jmcomic")
    IMAGE_CONCURRENCY: ClassVar[int] = 16

    def _get_option(self) -> object:
        if JmOption is None:
            raise ParseException("未安装 jmcomic 依赖库，请先安装后重载插件")
        option = JmOption.default()
        client = _option_client(option)
        proxy = self.proxy
        if proxy:
            postman = client.setdefault("postman", {})
            meta = postman.setdefault("meta_data", {})
            meta["proxies"] = {"https": proxy, "http": proxy}
        cookie = self.get_config_value("cookie")
        if cookie:
            postman = client.setdefault("postman", {})
            meta = postman.setdefault("meta_data", {})
            meta["headers"] = {"Cookie": cookie}
        return option

    @staticmethod
    def _blur(image_path: Path, radius: int = 5) -> Path:
        output_path = image_path.parent / f"{image_path.stem}_blur{image_path.suffix}"
        with Image.open(image_path) as image:
            blurred = image.filter(ImageFilter.GaussianBlur(radius=radius))
            blurred.save(output_path)
        return output_path

    @staticmethod
    def _decode_img(
        img_url: str,
        scramble_id: str,
        image_path: Path,
    ) -> Path:
        save_path = image_path.parent / f"{image_path.stem}_decode{image_path.suffix}"
        if save_path.exists() and save_path.stat().st_size > 0:
            return save_path
        if JmImageTool is None:
            return image_path
        split_num = JmImageTool.get_num_by_url(scramble_id, img_url)
        with Image.open(image_path) as image:
            JmImageTool.decode_and_save(split_num, image, str(save_path))
        return save_path

    @staticmethod
    def _imgs_to_pdf(img_paths: Sequence[Path], save_path: Path) -> Path:
        if not img_paths:
            raise ParseException("图片列表为空，可能需要检查 jm 号是否正确")
        images: list[Image.Image] = []
        try:
            for img_path in img_paths:
                with Image.open(img_path) as img:
                    images.append(img.convert("RGB"))
            images[0].save(
                save_path,
                "PDF",
                save_all=True,
                append_images=images[1:],
            )
        finally:
            for img in images:
                img.close()
        return save_path

    async def _download_all_photo(
        self,
        photo_detail: JmPhotoDetail,
    ) -> list[Path]:
        semaphore = asyncio.Semaphore(self.IMAGE_CONCURRENCY)

        async def download_and_decode(photo: JmPhotoItem) -> Path | None:
            async with semaphore:
                try:
                    raw_path = await self.downloader.download_image(photo.img_url, proxy=self.proxy)
                    return await asyncio.to_thread(
                        self._decode_img,
                        photo.img_url,
                        photo.scramble_id,
                        raw_path,
                    )
                except Exception as exc:
                    logger.warning(f"[JMComic] 图片下载或解密失败 | error={exc}")
                    return None

        results = await asyncio.gather(*(download_and_decode(photo) for photo in photo_detail))
        return [path for path in results if path is not None]

    async def _build_pdf(
        self,
        image_paths: asyncio.Task[list[Path]],
        comic_id: str,
    ) -> Path:
        paths = await image_paths
        if not paths:
            raise ParseException("漫画图片下载失败，未能获取到任何有效图片")
        pdf_path = self.downloader.cache_dir / f"{comic_id}.pdf"
        await asyncio.to_thread(self._imgs_to_pdf, paths, pdf_path)
        return pdf_path

    async def _fetch_comments(self, jm_client: JmAsyncClientProtocol, comic_id: str) -> tuple[Comment, ...]:
        include = self.get_config_value("include_comments", "true").lower() == "true"
        if not include:
            return ()
        limit = comment_limit(self.get_config_value("comment_limit", "5"))
        if limit <= 0:
            return ()
        try:
            page = await jm_client.album_pagination(
                comic_id,
                page=1,
                need_total=False,
            )
        except Exception as exc:
            logger.warning(f"[JMComic] 获取评论失败 | comic_id={comic_id} | error={exc}")
            return ()
        return decode_comments(page, limit=limit)

    @handle("18comic.vip/photo", r"18comic\.vip/photo/(?P<comic_id>\d{5,})")
    @handle("18comic.vip/album", r"18comic\.vip/album/(?P<comic_id>\d{5,})")
    @handle("jm", r"(?<![A-Za-z0-9])jm\s*(?P<comic_id>\d+)")
    @handle("JM", r"(?<![A-Za-z0-9])JM\s*(?P<comic_id>\d+)")
    @handle("Jm", r"(?<![A-Za-z0-9])Jm\s*(?P<comic_id>\d+)")
    async def _parse(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        comic_id = searched.group("comic_id").lstrip("0")
        if not comic_id:
            await self.send_info(
                title="错误",
                card_type="common",
                extra_info="JM 号不能为空",
            )
            return ()

        option = self._get_option()
        if not hasattr(option, "new_jm_async_client"):
            raise ParseException("当前 jmcomic 版本不支持异步客户端")
        client_factory = getattr(option, "new_jm_async_client")
        client_ctx = client_factory()

        async with client_ctx as jm_client:
            try:
                album_detail = await jm_client.get_album_detail(comic_id)
            except MissingAlbumPhotoException:
                await self.send_info(
                    title="错误",
                    card_type="common",
                    extra_info="当前 JM 号不存在",
                )
                return ()
            except JmcomicException as exc:
                raise ParseException("获取 jm 详情遇到问题") from exc

            album_title = album_detail.title
            album_name = album_detail.authoroname
            album_actors = album_detail.actors
            album_description = album_detail.description
            album_tags = album_detail.tags
            album_views = album_detail.views
            album_likes = album_detail.likes
            album_cover_url = JmcomicText.get_album_cover_url(comic_id) if JmcomicText else ""
            album_url = (
                JmcomicText.format_album_url(comic_id) if JmcomicText else f"https://18comic.vip/album/{comic_id}"
            )
            episode_count = len(album_detail.episode_list)

            photo_detail = await jm_client.get_photo_detail(comic_id)
            album_episode = photo_detail.sort
            comments = await self._fetch_comments(jm_client, comic_id)

            final_contents: list[ContentItem] = []
            cover_path: Path | None = None
            nsfw_mode = self.get_config_value("nsfw", "blur")
            if nsfw_mode != "ignore" and album_cover_url:
                cover_path = await self.downloader.download_image(album_cover_url, proxy=self.proxy)
                if nsfw_mode == "blur":
                    cover_path = await asyncio.to_thread(self._blur, cover_path)

            send_text = (
                f"标题: {album_title}\n"
                f"TAGS: {', '.join(f'#{tag}' for tag in album_tags)}\n"
                f"浏览: {album_views}\n"
                f"点赞: {album_likes}\n"
                f"描述: {album_description}\n"
            )
            extra_info = f"共 {episode_count} 话，当前下载第 {album_episode} 话\n漫画原链: {album_url}"
            if episode_count > 1:
                extra_info += "\n若需解析其他章节请发送对应章节jm号。"

            send_mode = self.get_config_value("image_send_mode", "pdf")
            if send_mode != "ignore":
                page_count = len(photo_detail)
                try:
                    max_page = int(self.get_config_value("max_page", "50"))
                except ValueError:
                    max_page = 50
                if max_page and page_count > max_page:
                    extra_info += f"\n当前漫画页数 {page_count}，超过最大页数 {max_page}，跳过下载。"
                else:
                    image_paths = asyncio.create_task(self._download_all_photo(photo_detail))
                    if send_mode == "pdf":
                        pdf_task = asyncio.create_task(self._build_pdf(image_paths, comic_id))
                        final_contents.append(self.create_file_content(loader=lambda: pdf_task))
                    elif send_mode == "merge":
                        downloaded = await image_paths
                        if not downloaded:
                            raise ParseException("漫画图片下载失败，未能获取到任何有效图片")
                        final_contents.extend(self.create_image_content(path=path) for path in downloaded)

            await self.send_info(
                title=album_name or album_title,
                card_type="video",
                author=self.create_author(", ".join(album_actors) if album_actors else "未知作者"),
                cover=cover_path,
                video_quality="漫画",
                text=send_text,
                extra_info=extra_info,
                comments=comments,
                url=album_url,
            )
            return self.result(final_contents)
