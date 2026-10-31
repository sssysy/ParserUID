"""默认皮肤的资源准备流程与模板上下文构建。"""

from __future__ import annotations

from pathlib import Path

import httpx

from ...models import Author, Metric, Comment, TextBlock, PreviewCard
from ...helpers import (
    IconName,
    icon,
    circle,
    data_uri,
    fit_cover,
    fit_width,
    read_rgba,
    fade_vertical,
    ambient_background,
    empty_image_placeholder,
)
from ...template_data import (
    TemplateCard,
    TemplateText,
    TemplateImage,
    TemplateReply,
    TemplateMetric,
    TemplateComment,
    TemplateImageGroup,
)
from ....utils.downloader import download_image
from ....utils.exceptions import DownloadException

CARD_WIDTH = 620
CARD_OUTPUT_WIDTH = 1000
VIDEO_WIDTH = 1000
LOGO_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "logos"


def _metric_icon_name(label: str) -> IconName:
    lookup: dict[str, IconName] = {
        "阅读": "eye",
        "观看": "eye",
        "播放": "play",
        "点赞": "heart",
        "喜欢": "heart",
        "评论": "comment",
        "收藏": "star",
        "追番": "star",
        "分享": "share",
        "粉丝": "heart",
        "关注": "heart",
    }
    return lookup[label] if label in lookup else "eye"


def _icon_dark(name: IconName) -> str:
    return data_uri(icon(name, (148, 163, 184, 255), 64))


def _icon_light(name: IconName) -> str:
    return data_uri(icon(name, (203, 213, 225, 210), 64))


def _metric_template(metric: Metric, *, dark: bool) -> TemplateMetric:
    name = _metric_icon_name(metric.label)
    uri = _icon_light(name) if dark else _icon_dark(name)
    return TemplateMetric(
        label=metric.label,
        value=metric.value,
        icon_uri=uri,
    )


async def _local_avatar(author: Author | None, *, proxy: str | None) -> tuple[str, Path | None]:
    if author is None:
        return "", None
    path = author.avatar_path
    if path is None and author.avatar_url is not None:
        try:
            path = (
                await download_image(author.avatar_url, proxy=proxy)
                if proxy
                else await download_image(author.avatar_url)
            )
        except (httpx.HTTPError, DownloadException, OSError, ValueError):
            return author.name, None
    return author.name, path


async def _avatar_data(author: Author | None, size: int, *, proxy: str | None) -> tuple[str, str]:
    name, path = await _local_avatar(author, proxy=proxy)
    if path is None:
        return name, ""
    avatar = read_rgba(path)
    return name, data_uri(circle(avatar, size))


def _file_uri(path: Path, size: tuple[int, int]) -> str:
    image = read_rgba(path)
    image = fit_cover(image, size)
    return data_uri(image)


def _content_image(path: Path) -> TemplateImage:
    image = fit_width(read_rgba(path), 1240)
    return TemplateImage(
        uri=data_uri(image),
        width=image.width,
        height=image.height,
    )


def _logo_uri(platform_name: str) -> str:
    safe_name = "".join(ch for ch in platform_name.lower() if ch.isalnum() or ch in "_-")
    path = LOGO_DIR / f"{safe_name}.png"
    if not path.is_file():
        return ""
    return _file_uri(path, (64, 64))


async def _comment_template(comment: Comment, *, proxy: str | None) -> TemplateComment:
    avatar_name, avatar_uri = await _avatar_data(
        Author(
            name=comment.author_name,
            avatar_path=comment.avatar_path,
            avatar_url=comment.avatar_url,
        ),
        120,
        proxy=proxy,
    )
    del avatar_name
    replies = []
    for reply in comment.replies:
        _, reply_uri = await _avatar_data(
            Author(
                name=reply.author_name,
                avatar_path=reply.avatar_path,
                avatar_url=reply.avatar_url,
            ),
            90,
            proxy=proxy,
        )
        replies.append(
            TemplateReply(
                author_name=reply.author_name,
                text=reply.text,
                avatar_uri=reply_uri,
                parent_author=reply.parent_author,
            )
        )
    return TemplateComment(
        author_name=comment.author_name,
        text=comment.text,
        avatar_uri=avatar_uri,
        location=comment.location,
        timestamp=comment.timestamp,
        like_icon_uri=_icon_dark("heart"),
        like_count=comment.like_count,
        reply_icon_uri=_icon_dark("comment"),
        reply_count=comment.reply_count,
        replies=tuple(replies),
    )


async def _comment_templates(
    comments: tuple[Comment, ...],
    *,
    proxy: str | None,
) -> tuple[TemplateComment, ...]:
    return tuple([await _comment_template(comment, proxy=proxy) for comment in comments])


def _image_group(images: list[TemplateImage]) -> TemplateImageGroup:
    if len(images) == 1:
        return TemplateImageGroup(
            images=(images[0],),
            columns=1,
            layout="single",
        )
    if len(images) == 2:
        return TemplateImageGroup(
            images=tuple(images),
            columns=2,
            layout="stacked",
        )
    columns = 3 if len(images) >= 3 else 2
    return TemplateImageGroup(
        images=tuple(images[:9]),
        columns=columns,
        layout="grid",
        hidden_count=max(0, len(images) - 9),
    )


async def _blocks(
    card: PreviewCard,
    *,
    proxy: str | None,
) -> tuple[TemplateText | TemplateImageGroup, ...]:
    result: list[TemplateText | TemplateImageGroup] = []
    pending: list[TemplateImage] = []

    def flush() -> None:
        if pending:
            result.append(_image_group(pending))
            pending.clear()

    for block in card.blocks:
        if isinstance(block, TextBlock):
            flush()
            result.append(TemplateText(block.text))
            continue
        path = block.path
        if path is None and block.url is not None:
            try:
                path = await download_image(block.url, proxy=proxy) if proxy else await download_image(block.url)
            except (httpx.HTTPError, DownloadException, OSError, ValueError):
                continue
        if path is None:
            raise ValueError("图片块没有可用来源")
        content_image = _content_image(path)
        pending.append(
            TemplateImage(
                uri=content_image.uri,
                width=content_image.width,
                height=content_image.height,
                caption=block.caption,
            )
        )
    flush()
    return tuple(result)


async def _hero_data(card: PreviewCard, *, dark: bool, proxy: str | None) -> str:
    path = card.cover_path
    if path is None and card.cover_url is not None:
        try:
            path = await download_image(card.cover_url, proxy=proxy) if proxy else await download_image(card.cover_url)
        except (httpx.HTTPError, DownloadException, OSError, ValueError):
            return ""
    if path is None:
        return ""
    source = read_rgba(path)
    if dark:
        hero = fit_width(source, VIDEO_WIDTH)
        return data_uri(fade_vertical(hero, top_ratio=0.12, bottom_ratio=0.28))
    cover = fit_width(source, 1240)
    cover = fade_vertical(cover, top_ratio=0.09, bottom_ratio=0.2)
    return data_uri(cover)


async def _ambient_data(card: PreviewCard, *, proxy: str | None) -> str:
    path = card.cover_path
    if path is None and card.cover_url is not None:
        try:
            path = await download_image(card.cover_url, proxy=proxy) if proxy else await download_image(card.cover_url)
        except (httpx.HTTPError, DownloadException, OSError, ValueError):
            return ""
    if path is None:
        return ""
    source = read_rgba(path)
    background = ambient_background(
        source,
        size=(VIDEO_WIDTH, 1600),
        blur=54,
        wash=(5, 7, 12, 155),
    )
    return data_uri(background)


async def prepare_template_card(
    card: PreviewCard,
    *,
    dark: bool,
    proxy: str | None = None,
) -> TemplateCard:
    """将平台预览卡片转换为默认皮肤的 Jinja 上下文。"""
    author_name, avatar_uri = await _avatar_data(
        card.author,
        150 if dark else 160,
        proxy=proxy,
    )
    if card.author is None:
        author_name = card.platform_display_name
    author_description = card.author.description if card.author is not None else None
    author_uid = card.author.uid if card.author is not None else None
    metrics = tuple(_metric_template(metric, dark=dark) for metric in card.metrics)
    author_metrics = (
        tuple(_metric_template(metric, dark=dark) for metric in card.author.metrics) if card.author is not None else ()
    )
    repost = await prepare_template_card(card.repost, dark=dark, proxy=proxy) if card.repost is not None else None
    blocks = await _blocks(card, proxy=proxy)
    hero_uri = await _hero_data(card, dark=dark, proxy=proxy)
    common_meta = tuple(item for item in (card.timestamp, author_description) if item)
    profile_note = " · ".join(
        item for item in (author_description or "", f"用户 ID：{author_uid}" if author_uid else "") if item
    )
    return TemplateCard(
        platform_display_name=card.platform_display_name,
        platform_logo_uri=_logo_uri(card.platform_name),
        title=card.title,
        text=card.text,
        author_name=author_name,
        author_description=author_description,
        author_uid=author_uid,
        author_avatar_uri=avatar_uri,
        author_metrics=author_metrics,
        timestamp=card.timestamp,
        source_url=card.source_url,
        hero_uri=hero_uri,
        ambient_uri=await _ambient_data(card, proxy=proxy) if dark else "",
        show_hero=bool(hero_uri) and not blocks,
        blocks=blocks,
        metrics=metrics,
        comments=await _comment_templates(card.comments, proxy=proxy),
        extra_info=card.extra_info,
        repost=repost,
        common_meta=common_meta,
        profile_note=profile_note,
        is_video=card.is_video,
        empty_image_placeholder=empty_image_placeholder((1240, 700)),
        play_icon_uri=data_uri(icon("play", (255, 255, 255, 235), 64)),
        clock_icon_uri=data_uri(icon("clock", (255, 255, 255, 220), 64)),
        video_duration=card.video_duration,
        video_quality=card.video_quality,
    )
