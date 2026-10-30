"""X 多媒体内容提取与卡片内容生成。"""

from __future__ import annotations

from collections.abc import Callable

from .models import XMedia, XTweet
from ...sender import ContentItem
from ...utils.models import TextContent


def media_content(
    media: XMedia,
    *,
    create_image: Callable[[str], ContentItem],
    create_video: Callable[..., ContentItem],
    create_dynamic: Callable[[str], ContentItem],
) -> ContentItem | None:
    if media.kind == "photo":
        return create_image(media.source_url)
    if media.kind == "animated_gif":
        return create_dynamic(media.source_url)
    best = media.best_variant
    if best is None:
        return None
    fallbacks = tuple(
        item.url
        for item in sorted(media.mp4_variants, key=lambda item: item.bitrate, reverse=True)
        if item.url != best.url
    )
    return create_video(
        best.url,
        cover_url=media.cover_url or None,
        duration=media.duration,
        fallback_urls=fallbacks,
    )


def tweet_contents(
    tweet: XTweet,
    *,
    create_image: Callable[[str], ContentItem],
    create_video: Callable[..., ContentItem],
    create_dynamic: Callable[[str], ContentItem],
    create_graphic: Callable[[str], ContentItem],
) -> list[ContentItem]:
    contents: list[ContentItem] = []
    if tweet.article_cover_url:
        contents.append(create_graphic(tweet.article_cover_url))
    if tweet.article_blocks:
        for block in tweet.article_blocks:
            if block.kind == "text" and block.text:
                contents.append(TextContent(block.text))
            elif block.media is not None:
                item = media_content(
                    block.media,
                    create_image=create_image,
                    create_video=create_video,
                    create_dynamic=create_dynamic,
                )
                if item is not None:
                    contents.append(item)
    elif tweet.text:
        contents.append(TextContent(tweet.text))
    for media in tweet.media:
        item = media_content(
            media,
            create_image=create_image,
            create_video=create_video,
            create_dynamic=create_dynamic,
        )
        if item is not None:
            contents.append(item)
    return contents


def description_contents(tweet: XTweet, *, translation: str = "") -> list[str]:
    """收集卡片显示的文本段落。"""
    lines: list[str] = []
    if tweet.article_blocks:
        for block in tweet.article_blocks:
            if block.kind == "text" and block.text:
                lines.append(block.text)
    elif tweet.text:
        lines.append(tweet.text)
    if translation:
        lines.append(f"Grok 翻译：\n{translation}")
    return lines


def first_image_url(tweet: XTweet) -> str:
    if tweet.article_cover_url:
        return tweet.article_cover_url
    for media in tweet.media:
        if media.kind == "photo" and media.source_url:
            return media.source_url
    return ""


def first_cover_url(tweet: XTweet) -> str:
    """卡片优先使用静态封面或图片。"""
    for media in tweet.media:
        if media.kind == "photo" and media.source_url:
            return media.source_url
    for media in tweet.media:
        if media.cover_url:
            return media.cover_url
    return tweet.article_cover_url
