"""跨平台解析业务通用函数。"""

from __future__ import annotations

from ..render.models import CardBlock, TextBlock, ImageBlock


def build_image_blocks(
    urls: list[str],
    *,
    limit: int = 0,
) -> tuple[ImageBlock, ...]:
    """把图片地址转为正文图片块，limit 大于 0 时限制卡片图片数量。"""
    blocks: list[ImageBlock] = []
    for url in urls:
        if not url:
            continue
        if 0 < limit <= len(blocks):
            break
        blocks.append(ImageBlock(url=url))
    return tuple(blocks)


def build_card_blocks(
    segments: list[tuple[str, str]],
    *,
    limit: int = 0,
) -> tuple[CardBlock, ...]:
    """把有序的 text/image 段合并为卡片正文块，保持原始顺序。"""
    merged: list[tuple[str, str]] = []
    for kind, value in segments:
        if not value:
            continue
        if kind == "text" and merged and merged[-1][0] == "text":
            merged[-1] = ("text", f"{merged[-1][1]}\n\n{value}")
            continue
        merged.append((kind, value))

    blocks: list[CardBlock] = []
    images = 0
    for kind, value in merged:
        if kind == "image":
            if 0 < limit <= images:
                continue
            images += 1
            blocks.append(ImageBlock(url=value))
            continue
        blocks.append(TextBlock(value))
    return tuple(blocks)
