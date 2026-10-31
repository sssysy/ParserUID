from __future__ import annotations

import io
import base64
import hashlib
from typing import Literal, BinaryIO
from pathlib import Path
from collections.abc import Iterator

from PIL import Image, ImageOps, ImageDraw, ImageFilter, ImageEnhance

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_EDGE = 2400

IconName = Literal[
    "eye",
    "heart",
    "comment",
    "star",
    "play",
    "clock",
    "share",
]


def encode_png(image: Image.Image) -> bytes:
    """将 RGBA 图片无损编码，供 data URI 嵌入使用。"""
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def data_uri(image: Image.Image) -> str:
    """返回 PNG 格式的 data URI，不暴露本地路径。"""
    encoded = encode_png(image)
    return f"data:image/png;base64,{base64.b64encode(encoded).decode('ascii')}"


def read_rgba(path: Path) -> Image.Image:
    """读取限定大小的图片，应用 EXIF 方向后返回 RGBA 图像。"""
    if not path.is_file():
        raise FileNotFoundError(f"图片不可用：{path}")
    stat = path.stat()
    if stat.st_size <= 0 or stat.st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"图片大小超出允许范围：{stat.st_size}")
    with Image.open(path) as source:
        source.seek(0)
        image = ImageOps.exif_transpose(source).convert("RGBA")
    if max(image.size) > MAX_SOURCE_EDGE:
        image.thumbnail(
            (MAX_SOURCE_EDGE, MAX_SOURCE_EDGE),
            Image.Resampling.LANCZOS,
        )
    return image


def file_digest(path: Path) -> str:
    """返回渲染缓存使用的内容摘要。"""
    if not path.is_file():
        raise FileNotFoundError(f"图片不可用：{path}")
    stat = path.stat()
    if stat.st_size <= 0 or stat.st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"图片大小超出允许范围：{stat.st_size}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in _chunks(handle):
            digest.update(chunk)
    return digest.hexdigest()


def _chunks(handle: BinaryIO) -> Iterator[bytes]:
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            return
        yield chunk


def fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """缩放并居中裁切到指定尺寸。"""
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)


def fit_width(image: Image.Image, width: int) -> Image.Image:
    """等比缩放到指定宽度，完整保留原图高度。"""
    if image.width <= 0 or width <= 0:
        raise ValueError("图片宽度必须大于 0")
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def fit_contain(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """等比缩放到指定尺寸内，不裁切图片。"""
    contained = ImageOps.contain(
        image,
        size,
        method=Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    left = (size[0] - contained.width) // 2
    top = (size[1] - contained.height) // 2
    canvas.alpha_composite(contained, (left, top))
    return canvas


def circle(image: Image.Image, size: int) -> Image.Image:
    """居中裁切图片并应用圆形蒙版。"""
    square = fit_cover(image, (size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    square.putalpha(mask)
    return square


def round_rect(image: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    """居中裁切图片并应用圆角蒙版。"""
    fitted = fit_cover(image, size)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    fitted.putalpha(mask)
    return fitted


def fade_vertical(
    image: Image.Image,
    *,
    top_ratio: float,
    bottom_ratio: float,
) -> Image.Image:
    """应用垂直方向的线性透明度渐变。"""
    canvas = image.convert("RGBA")
    width, height = canvas.size
    top = max(1, round(height * top_ratio))
    bottom = max(1, round(height * bottom_ratio))
    alpha = Image.new("L", canvas.size)
    draw = ImageDraw.Draw(alpha)
    for y in range(height):
        if y < top:
            value = round(255 * y / top)
        elif y >= height - bottom:
            value = round(255 * (height - y - 1) / bottom)
        else:
            value = 255
        draw.line((0, y, width, y), fill=max(0, min(255, value)))
    canvas.putalpha(alpha)
    return canvas


def ambient_background(
    image: Image.Image,
    *,
    size: tuple[int, int],
    blur: float,
    wash: tuple[int, int, int, int],
) -> Image.Image:
    """将模糊与色彩覆盖效果合成为一张背景图。"""
    background = fit_cover(image.convert("RGB"), size)
    background = background.filter(ImageFilter.GaussianBlur(blur))
    background = ImageEnhance.Color(background).enhance(1.75)
    canvas = background.convert("RGBA")
    overlay = Image.new("RGBA", size, wash)
    return Image.alpha_composite(canvas, overlay)


def grid_overlay(size: tuple[int, int], step: int) -> Image.Image:
    """绘制视频皮肤使用的浅色网格。"""
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for x in range(0, size[0], step):
        draw.line((x, 0, x, size[1]), fill=(255, 255, 255, 8), width=1)
    for y in range(0, size[1], step):
        draw.line((0, y, size[0], y), fill=(255, 255, 255, 8), width=1)
    return overlay


def empty_image_placeholder(size: tuple[int, int]) -> str:
    """卡片没有可用图片时，生成中性占位图。"""
    image = Image.new("RGBA", size, (241, 245, 249, 255))
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, size[0], size[1]), fill=(226, 232, 240, 255), width=2)
    draw.line((size[0], 0, 0, size[1]), fill=(226, 232, 240, 255), width=2)
    return data_uri(image)


def icon(name: IconName, color: tuple[int, int, int, int], size: int = 64) -> Image.Image:
    """按指定尺寸绘制一个图标。"""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    scale = size / 64

    def point(value: float) -> int:
        return round(value * scale)

    if name == "eye":
        draw.ellipse((point(5), point(18), point(59), point(46)), outline=color, width=max(2, point(6)))
        draw.ellipse((point(23), point(22), point(41), point(40)), fill=color)
    elif name == "heart":
        draw.ellipse((point(8), point(13), point(32), point(37)), fill=color)
        draw.ellipse((point(32), point(13), point(56), point(37)), fill=color)
        draw.polygon(
            (
                (point(10), point(31)),
                (point(54), point(31)),
                (point(32), point(56)),
            ),
            fill=color,
        )
    elif name == "comment":
        draw.rounded_rectangle(
            (point(7), point(10), point(57), point(46)),
            radius=point(14),
            fill=color,
        )
        draw.polygon(
            (
                (point(18), point(43)),
                (point(22), point(58)),
                (point(34), point(45)),
            ),
            fill=color,
        )
        for x in (23, 32, 41):
            draw.ellipse(
                (point(x - 3), point(25), point(x + 3), point(31)),
                fill=(255, 255, 255, 255),
            )
    elif name == "star":
        draw.polygon(
            (
                (point(32), point(5)),
                (point(40), point(23)),
                (point(59), point(25)),
                (point(44), point(38)),
                (point(49), point(57)),
                (point(32), point(47)),
                (point(15), point(57)),
                (point(20), point(38)),
                (point(5), point(25)),
                (point(24), point(23)),
            ),
            fill=color,
        )
    elif name == "play":
        draw.polygon(
            (
                (point(22), point(10)),
                (point(56), point(32)),
                (point(22), point(54)),
            ),
            fill=color,
        )
    elif name == "clock":
        draw.ellipse((point(5), point(5), point(59), point(59)), outline=color, width=max(2, point(6)))
        draw.line(
            (point(32), point(16), point(32), point(34), point(44), point(40)),
            fill=color,
            width=max(2, point(6)),
            joint="curve",
        )
    elif name == "share":
        draw.ellipse((point(5), point(25), point(21), point(41)), fill=color)
        draw.ellipse((point(43), point(5), point(59), point(21)), fill=color)
        draw.ellipse((point(43), point(43), point(59), point(59)), fill=color)
        draw.line((point(19), point(29), point(46), point(15)), fill=color, width=max(2, point(6)))
        draw.line((point(19), point(37), point(46), point(51)), fill=color, width=max(2, point(6)))
    return image
