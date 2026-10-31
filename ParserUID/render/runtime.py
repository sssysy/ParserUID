from __future__ import annotations

import io
import json
import asyncio
import inspect
import importlib
import importlib.util
from typing import Final
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Mapping, Callable, Awaitable

import httpx
from PIL import Image, ImageDraw
from jinja2 import Template, Environment, FileSystemLoader, select_autoescape

from gsuid_core.data_store import get_res_path

from .models import (
    Author,
    Comment,
    CardBlock,
    TextBlock,
    ImageBlock,
    PreviewCard,
    CommentReply,
)
from .helpers import (
    MAX_RENDER_EDGE,
    encode_png,
    file_digest,
)
from ..utils.downloader import download_image
from ..utils.exceptions import DownloadException

CARD_WIDTH: Final = 620
CARD_OUTPUT_WIDTH: Final = 1000
VIDEO_WIDTH: Final = 1000
COMMON_DPI: Final = 96.0 * (CARD_OUTPUT_WIDTH / CARD_WIDTH)
VIDEO_DPI: Final = 96.0
PACK_DIR: Final = Path(__file__).resolve().parent / "pack"
CACHE_DIR: Final = get_res_path("ParserUID") / "render_cache"
RENDER_CACHE_MAX_AGE_SECONDS: Final = 7 * 24 * 60 * 60
PREVIEW_FADE_HEIGHT: Final = 320
PREVIEW_DOT_RADIUS: Final = 8
PREVIEW_DOT_GAP: Final = 22
PREVIEW_DOT_BOTTOM: Final = 48
PREVIEW_LIGHT_BACKGROUND: Final = (248, 250, 252)
PREVIEW_DARK_BACKGROUND: Final = (8, 9, 11)
PREVIEW_LIGHT_DOT: Final = (148, 163, 184)
PREVIEW_DARK_DOT: Final = (226, 232, 240)
_RENDER_VERSION: Final = "15"
_SKIN_NAME_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


@dataclass(frozen=True, slots=True)
class SkinManifest:
    name: str
    display_name: str
    templates: Mapping[str, str]


def _manifest_string(raw: Mapping[str, object], key: str) -> str:
    if key not in raw:
        raise ValueError(f"皮肤 manifest 缺少字段：{key}")
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"皮肤 manifest 字段必须是字符串：{key}")
    return value.strip()


def _manifest_templates(raw: Mapping[str, object]) -> Mapping[str, str]:
    if "templates" not in raw:
        raise ValueError("皮肤 manifest 缺少 templates")
    value = raw["templates"]
    if not isinstance(value, dict):
        raise ValueError("皮肤 manifest 的 templates 必须是对象")
    templates: dict[str, str] = {}
    for kind, template_name in value.items():
        if not isinstance(kind, str) or not isinstance(template_name, str):
            raise ValueError("皮肤 templates 的键和值必须是字符串")
        relative = Path(template_name)
        if (
            not template_name
            or relative.is_absolute()
            or relative.suffix.lower() != ".html"
            or ".." in relative.parts
            or len(relative.parts) != 1
        ):
            raise ValueError(f"皮肤模板必须是目录内的 .html 文件名：{template_name}")
        templates[kind] = template_name
    if "default" not in templates:
        raise ValueError("皮肤 templates 必须包含 default")
    return templates


def load_skin_manifest(pack_dir: Path) -> SkinManifest:
    """Read and validate one skin manifest."""
    manifest_file = pack_dir / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError(f"皮肤缺少 manifest.json：{pack_dir.name}")
    raw: object = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"皮肤 manifest 必须是对象：{pack_dir.name}")
    return SkinManifest(
        name=_manifest_string(raw, "name"),
        display_name=_manifest_string(raw, "display_name"),
        templates=_manifest_templates(raw),
    )


def _template_candidates(card: PreviewCard, manifest: SkinManifest) -> tuple[str, ...]:
    """Choose safe HTML template names in deterministic fallback order."""
    requested: list[str] = []
    kind = "video" if card.is_video else "common"
    platform_template = f"{card.platform_name}.html"
    if platform_template in manifest.templates.values():
        requested.append(platform_template)
    if kind in manifest.templates:
        requested.append(manifest.templates[kind])
    requested.append(manifest.templates["default"])
    candidates: list[str] = []
    for template_name in requested:
        if template_name not in candidates:
            candidates.append(template_name)
    return tuple(candidates)


def _skin_environment(pack_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(pack_dir),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _skin_name_is_safe(skin_name: str) -> bool:
    return bool(skin_name) and all(char in _SKIN_NAME_ALLOWED for char in skin_name)


async def _prepare_context(
    card: PreviewCard,
    pack_dir: Path,
    skin_name: str,
    *,
    proxy: str | None,
) -> Mapping[str, object]:
    """Load optional context.py data preparation; HTML stays in Jinja templates."""
    context_file = pack_dir / "context.py"
    if not context_file.is_file():
        return {"card": card}
    module_name = f"{__package__}.pack.{skin_name}.context"
    spec = importlib.util.spec_from_file_location(module_name, context_file)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载皮肤 context.py：{pack_dir.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if "prepare_context" not in module.__dict__:
        raise ValueError(f"皮肤 context.py 缺少 prepare_context：{pack_dir.name}")
    function = module.__dict__["prepare_context"]
    if not callable(function):
        raise ValueError(f"prepare_context 不可调用：{pack_dir.name}")
    accepts_proxy = "proxy" in inspect.signature(function).parameters
    prepare: Callable[..., Awaitable[Mapping[str, object]] | Mapping[str, object]] = function
    if inspect.iscoroutinefunction(prepare):
        return await prepare(card, proxy=proxy) if accepts_proxy else await prepare(card)
    blocking = asyncio.to_thread(prepare, card, proxy=proxy) if accepts_proxy else asyncio.to_thread(prepare, card)
    result = await blocking
    return await result if inspect.isawaitable(result) else result


async def _download_asset_safely(
    url: str | None,
    current_path: Path | None,
    *,
    proxy: str | None,
) -> Path | None:
    if current_path is not None and current_path.is_file():
        return current_path
    if url is None:
        return None
    try:
        if proxy is None:
            return await download_image(url)
        return await download_image(url, proxy=proxy)
    except (httpx.HTTPError, DownloadException, OSError, ValueError):
        return None


async def ensure_card_assets(card: PreviewCard, *, proxy: str | None = None) -> PreviewCard:
    """预取远程媒体资源，让皮肤模板直接使用本地路径。"""
    cover_path = await _download_asset_safely(card.cover_url, card.cover_path, proxy=proxy)

    author = card.author
    if author is not None:
        avatar_path = await _download_asset_safely(author.avatar_url, author.avatar_path, proxy=proxy)
        author = Author(
            name=author.name,
            description=author.description,
            uid=author.uid,
            avatar_path=avatar_path,
            avatar_url=author.avatar_url,
            metrics=author.metrics,
        )

    blocks: list[CardBlock] = []
    for block in card.blocks:
        if isinstance(block, TextBlock):
            blocks.append(block)
        elif isinstance(block, ImageBlock):
            block_path = await _download_asset_safely(block.url, block.path, proxy=proxy)
            blocks.append(ImageBlock(path=block_path, caption=block.caption) if block_path is not None else block)

    comments: list[Comment] = []
    for comment in card.comments:
        c_avatar = await _download_asset_safely(comment.avatar_url, comment.avatar_path, proxy=proxy)
        replies: list[CommentReply] = []
        for reply in comment.replies:
            r_avatar = await _download_asset_safely(reply.avatar_url, reply.avatar_path, proxy=proxy)
            replies.append(
                CommentReply(
                    author_name=reply.author_name,
                    text=reply.text,
                    avatar_path=r_avatar,
                    avatar_url=reply.avatar_url,
                    parent_author=reply.parent_author,
                )
            )
        comments.append(
            Comment(
                author_name=comment.author_name,
                text=comment.text,
                avatar_path=c_avatar,
                avatar_url=comment.avatar_url,
                location=comment.location,
                timestamp=comment.timestamp,
                like_count=comment.like_count,
                reply_count=comment.reply_count,
                replies=tuple(replies),
            )
        )

    repost = await ensure_card_assets(card.repost, proxy=proxy) if card.repost is not None else None

    return PreviewCard(
        platform_name=card.platform_name,
        platform_display_name=card.platform_display_name,
        title=card.title,
        text=card.text,
        author=author,
        timestamp=card.timestamp,
        source_url=card.source_url,
        cover_path=cover_path,
        cover_url=card.cover_url,
        blocks=tuple(blocks),
        metrics=card.metrics,
        comments=tuple(comments),
        extra_info=card.extra_info,
        repost=repost,
        video_duration=card.video_duration,
        video_quality=card.video_quality,
        card_type=card.card_type,
    )


def load_skin_pack(skin_name: str) -> tuple[Path, SkinManifest, Environment]:
    """Load one skin directory, manifest, and Jinja environment."""
    if not _skin_name_is_safe(skin_name):
        raise ValueError(f"不支持的皮肤：{skin_name}")
    pack_dir = PACK_DIR / skin_name
    if not pack_dir.is_dir():
        raise ValueError(f"不支持的皮肤：{skin_name}")
    manifest = load_skin_manifest(pack_dir)
    if manifest.name != skin_name:
        raise ValueError(f"皮肤目录名与 manifest.name 不一致：{skin_name} != {manifest.name}")
    return pack_dir, manifest, _skin_environment(pack_dir)


def _render_skin_html(
    card: PreviewCard,
    pack_dir: Path,
    manifest: SkinManifest,
    environment: Environment,
    context: Mapping[str, object],
) -> str:
    for template_name in _template_candidates(card, manifest):
        template: Template = environment.get_template(template_name)
        return template.render(**context)
    raise ValueError(f"皮肤没有可用的 HTML 模板：{pack_dir.name}")


def _render_prepared_html(html: str, *, video: bool) -> bytes:
    import pytakumi

    from gsuid_core.utils.html_render import _dpr_from_dpi, _font_families, _ensure_renderer

    renderer = _ensure_renderer()
    width = VIDEO_WIDTH if video else CARD_OUTPUT_WIDTH
    dpi = VIDEO_DPI if video else COMMON_DPI
    layout_width = VIDEO_WIDTH if video else CARD_WIDTH
    ratio = _dpr_from_dpi(dpi) or 1.0
    max_css_height = MAX_RENDER_EDGE / ratio
    max_height_css = f".pytakumi-root{{max-width:{layout_width}px;max-height:{max_css_height:g}px;overflow:hidden}}"
    image_bytes = pytakumi.html_to_pic(
        html,
        width=width,
        height=None,
        format="png",
        css=max_height_css,
        renderer=renderer,
        device_pixel_ratio=_dpr_from_dpi(dpi),
        font_families=_font_families(None),
        lang="zh",
    )
    if _png_height(image_bytes) < MAX_RENDER_EDGE:
        return image_bytes
    if not _html_exceeds_limit(
        html,
        renderer=renderer,
        width=width,
        layout_width=layout_width,
        ratio=ratio,
    ):
        return image_bytes
    return _apply_preview_cutoff(image_bytes, video=video)


def _png_height(image_bytes: bytes) -> int:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return image.height


def _html_exceeds_limit(
    html: str,
    *,
    renderer: object,
    width: int,
    layout_width: int,
    ratio: float,
) -> bool:
    """测量未被截断的布局高度，避免恰好撑满时误加省略提示。"""
    import pytakumi
    from pytakumi.api import extract_styles_and_body

    if not isinstance(renderer, pytakumi.Renderer):
        raise TypeError("渲染器类型不受支持")
    styles, body = extract_styles_and_body(
        html,
        width,
        None,
        device_pixel_ratio=ratio,
    )
    measured = renderer.measure(
        pytakumi.from_html(body),
        width=layout_width,
        stylesheets=styles,
    )
    height = measured.get("height")
    if isinstance(height, bool) or not isinstance(height, int | float):
        raise ValueError("渲染器未返回有效布局高度")
    return float(height) * ratio > MAX_RENDER_EDGE + 0.5


def _apply_preview_cutoff(image_bytes: bytes, *, video: bool) -> bytes:
    """把超过渲染上限的卡片裁到 8192 像素，并在底部渐隐提示。"""
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGBA")
    if image.height > MAX_RENDER_EDGE:
        image = image.crop((0, 0, image.width, MAX_RENDER_EDGE))
    if image.height < MAX_RENDER_EDGE:
        return image_bytes

    background = PREVIEW_DARK_BACKGROUND if video else PREVIEW_LIGHT_BACKGROUND
    dot_color = PREVIEW_DARK_DOT if video else PREVIEW_LIGHT_DOT
    fade_height = min(PREVIEW_FADE_HEIGHT, image.height)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fade_top = image.height - fade_height
    for offset in range(fade_height):
        progress = (offset + 1) / fade_height
        alpha = round(255 * progress**1.8)
        draw.line(
            (0, fade_top + offset, image.width, fade_top + offset),
            fill=(*background, alpha),
        )
    image = Image.alpha_composite(image, overlay)

    draw = ImageDraw.Draw(image)
    center_y = image.height - PREVIEW_DOT_BOTTOM
    center_x = image.width // 2
    for index in (-1, 0, 1):
        x = center_x + index * PREVIEW_DOT_GAP
        draw.ellipse(
            (
                x - PREVIEW_DOT_RADIUS,
                center_y - PREVIEW_DOT_RADIUS,
                x + PREVIEW_DOT_RADIUS,
                center_y + PREVIEW_DOT_RADIUS,
            ),
            fill=(*dot_color, 255),
        )
    return encode_png(image)


async def render_preview(
    card: PreviewCard,
    *,
    skin: str = "default",
    proxy: str | None = None,
) -> Path:
    """准备资源，用指定皮肤渲染卡片，并返回缓存的 PNG 路径。"""
    return await _render_preview(card, skin=skin, proxy=proxy)


async def _render_preview(card: PreviewCard, *, skin: str, proxy: str | None) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dark = card.is_video
    digest = _digest_card(card, skin)
    path = CACHE_DIR / f"{sheet_name(dark)}_{digest}.png"
    if path.is_file():
        return path

    ready_card = await ensure_card_assets(card, proxy=proxy)
    pack_dir, manifest, environment = load_skin_pack(skin)
    context = await _prepare_context(ready_card, pack_dir, skin, proxy=proxy)
    html = await asyncio.to_thread(
        _render_skin_html,
        ready_card,
        pack_dir,
        manifest,
        environment,
        context,
    )

    image_bytes = await asyncio.to_thread(_render_prepared_html, html, video=dark)
    await asyncio.to_thread(path.write_bytes, image_bytes)
    return path


async def clean_render_cache(*, max_age_seconds: int = RENDER_CACHE_MAX_AGE_SECONDS) -> int:
    """删除过期的渲染卡片并返回删除数量。"""
    if not CACHE_DIR.is_dir():
        return 0
    import time

    cutoff = time.time() - max(0, max_age_seconds)
    removed = 0
    for path in CACHE_DIR.iterdir():
        if not path.is_file() or path.stat().st_mtime >= cutoff:
            continue
        await asyncio.to_thread(path.unlink, missing_ok=True)
        removed += 1
    return removed


async def close_render_assets() -> None:
    """在插件关闭时释放渲染器持有的资源。"""
    # 共享渲染器按进程复用，没有关闭接口。


def sheet_name(video: bool) -> str:
    return "video" if video else "common"


def _digest_card(card: PreviewCard, skin: str) -> str:
    import hashlib

    parts = [
        _RENDER_VERSION,
        skin,
        card.platform_name,
        card.title,
        card.text,
        _source_digest(card.cover_path, card.cover_url),
        str(card.timestamp or ""),
        str(card.source_url or ""),
        str(card.extra_info or ""),
        str(card.video_duration or ""),
        str(card.video_quality or ""),
    ]
    if card.author is not None:
        parts.extend(
            (
                card.author.name,
                card.author.description or "",
                card.author.uid or "",
                _source_digest(card.author.avatar_path, card.author.avatar_url),
            )
        )
        parts.extend(f"am:{item.label}:{item.value}" for item in card.author.metrics)
    parts.extend(f"m:{item.label}:{item.value}" for item in card.metrics)
    for comment in card.comments:
        parts.extend(
            (
                comment.author_name,
                comment.text,
                comment.location or "",
                comment.timestamp or "",
                comment.like_count or "",
                comment.reply_count or "",
                _source_digest(comment.avatar_path, comment.avatar_url),
            )
        )
        for reply in comment.replies:
            parts.extend(
                (
                    reply.author_name,
                    reply.text,
                    reply.parent_author or "",
                    _source_digest(reply.avatar_path, reply.avatar_url),
                )
            )
    for block in card.blocks:
        if isinstance(block, TextBlock):
            parts.append("t:" + block.text)
        else:
            parts.extend(("i:", _source_digest(block.path, block.url), block.caption or ""))
    if card.repost is not None:
        parts.append("repost:")
        parts.append(_digest_card(card.repost, skin))
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()[:16]


def _path_digest(path: Path | None) -> str:
    if path is None:
        return ""
    return f"{path.name}:{file_digest(path)}"


def _source_digest(path: Path | None, url: str | None) -> str:
    if path is not None:
        return _path_digest(path)
    return f"url:{url or ''}"
