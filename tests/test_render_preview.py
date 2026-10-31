"""ParserUID 预览渲染的离线检查。"""

from __future__ import annotations

import io
import json
import asyncio
import base64
from pathlib import Path

import pytest
from PIL import Image

from ParserUID.utils import downloader as image_download
from ParserUID.render import helpers, runtime as render_runtime
from ParserUID.render.models import (
    Author,
    Metric,
    Comment,
    TextBlock,
    ImageBlock,
    PreviewCard,
    CommentReply,
)
from ParserUID.render.template_data import TemplateCard
from ParserUID.render.runtime import render_preview
from ParserUID.utils.exceptions import DownloadException
from ParserUID.render.template_data import CARD_FOOTER
from ParserUID.render.pack.default.context import prepare_context


def _image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def _card(tmp_path: Path) -> PreviewCard:
    cover = _image(tmp_path / "cover.png", (640, 360), (240, 120, 140))
    avatar = _image(tmp_path / "avatar.png", (120, 120), (60, 110, 220))
    image_a = _image(tmp_path / "a.png", (300, 300), (40, 90, 160))
    image_b = _image(tmp_path / "b.png", (300, 300), (120, 60, 180))
    return PreviewCard(
        platform_name="bilibili",
        platform_display_name="哔哩哔哩",
        title="渲染器离线测试",
        text="第一段正文。\n第二段正文。",
        author=Author(
            name="测试作者",
            description="测试签名",
            uid="123",
            avatar_path=avatar,
            metrics=(Metric("粉丝", "10万"),),
        ),
        timestamp="2026-10-30 12:00",
        cover_path=cover,
        blocks=(
            TextBlock("块级文本。"),
            ImageBlock(image_a),
            ImageBlock(image_b),
        ),
        metrics=(Metric("浏览", "1000"), Metric("点赞", "20")),
        comments=(
            Comment(
                author_name="评论者",
                text="评论正文。",
                avatar_path=avatar,
                replies=(
                    CommentReply(
                        author_name="回复者",
                        text="回复正文。",
                        avatar_path=avatar,
                        parent_author="评论者",
                    ),
                ),
            ),
        ),
        extra_info="补充说明。",
    )


def _video(card: PreviewCard) -> PreviewCard:
    return PreviewCard(
        platform_name=card.platform_name,
        platform_display_name=card.platform_display_name,
        title=card.title,
        text=card.text,
        author=card.author,
        timestamp=card.timestamp,
        cover_path=card.cover_path,
        blocks=card.blocks,
        metrics=card.metrics,
        comments=card.comments,
        extra_info=card.extra_info,
        video_duration="03:24",
        video_quality="1080P",
    )


def _assert_png(path: Path) -> None:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(data)) as image:
        assert image.width == 1000
        assert image.height > 300


def _decode_data_uri(uri: str) -> Image.Image:
    encoded = uri.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(encoded)))


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render_runtime, "CACHE_DIR", tmp_path / "render_cache")
    monkeypatch.setattr(image_download, "CACHE_DIR", tmp_path / "img_cache")


def test_render_common_card(tmp_path: Path) -> None:
    path = asyncio.run(render_preview(_card(tmp_path)))
    _assert_png(path)


def test_render_video_card(tmp_path: Path) -> None:
    path = asyncio.run(render_preview(_video(_card(tmp_path))))
    _assert_png(path)


def test_render_cache_is_content_addressed(tmp_path: Path) -> None:
    card = _card(tmp_path)
    first = asyncio.run(render_preview(card))
    second = asyncio.run(render_preview(card))
    assert first == second


def test_render_survives_avatar_download_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failed_url = "http://i0.hdslb.com/bfs/face/failed.jpg"

    async def fail_download(url: str) -> Path:
        raise DownloadException(f"文件下载失败：{url}")

    monkeypatch.setattr(render_runtime, "download_image", fail_download)
    monkeypatch.setattr("ParserUID.render.pack.default.pipeline.download_image", fail_download)
    card = _card(tmp_path)
    card = PreviewCard(
        platform_name=card.platform_name,
        platform_display_name=card.platform_display_name,
        title=card.title,
        text=card.text,
        author=Author(name="测试作者", avatar_url=failed_url),
        timestamp=card.timestamp,
        cover_path=card.cover_path,
        blocks=card.blocks,
        metrics=card.metrics,
        comments=card.comments,
        extra_info=card.extra_info,
    )

    path = asyncio.run(render_preview(card))

    _assert_png(path)


def test_clean_render_cache(tmp_path: Path) -> None:
    path = asyncio.run(render_preview(_card(tmp_path)))
    assert path.is_file()
    removed = asyncio.run(render_runtime.clean_render_cache(max_age_seconds=0))
    assert removed >= 1
    assert not path.is_file()


def test_card_copy_and_footer(tmp_path: Path) -> None:
    card = _card(tmp_path)
    ready_card = asyncio.run(render_runtime.ensure_card_assets(card))
    context = asyncio.run(prepare_context(ready_card))
    _, _, environment = render_runtime.load_skin_pack("default")
    common = environment.get_template("common.html").render(**context)
    video = environment.get_template("video.html").render(**context)
    assert "Create by GsCore &amp; Power by ParserUID" in common
    assert "用户 ID：123" in common
    assert "用户 ID：123" in video
    assert "由 GsCore 提供核心支持" not in common
    assert "ID:" not in common
    assert ">ID " not in video
    assert CARD_FOOTER == "Create by GsCore & Power by ParserUID"


def test_tall_content_image_keeps_full_height(tmp_path: Path) -> None:
    tall = _image(tmp_path / "tall.png", (560, 1400), (40, 90, 160))
    card = PreviewCard(
        platform_name="tieba",
        platform_display_name="百度贴吧",
        title="竖图完整显示",
        blocks=(ImageBlock(tall),),
    )

    ready_card = asyncio.run(render_runtime.ensure_card_assets(card))
    context = asyncio.run(prepare_context(ready_card))
    _, _, environment = render_runtime.load_skin_pack("default")
    html = environment.get_template("common.html").render(**context)

    assert 'width="1240"' in html
    assert 'height="3100"' in html
    single_image_start = html.index('<div class="single-image">')
    single_image_end = html.index("</div>", single_image_start)
    assert "object-fit:cover" not in html[single_image_start:single_image_end]


def test_tall_cover_keeps_full_height(tmp_path: Path) -> None:
    tall = _image(tmp_path / "tall_cover.png", (560, 1400), (40, 90, 160))
    card = PreviewCard(
        platform_name="tieba",
        platform_display_name="百度贴吧",
        title="竖版封面完整显示",
        cover_path=tall,
    )

    ready_card = asyncio.run(render_runtime.ensure_card_assets(card))
    context = asyncio.run(prepare_context(ready_card))
    template_card = context["card"]
    assert isinstance(template_card, TemplateCard)
    assert template_card.show_hero
    with _decode_data_uri(template_card.hero_uri) as image:
        assert image.width == 1240
        assert image.height == 3100


def test_tall_video_cover_keeps_full_height(tmp_path: Path) -> None:
    tall = _image(tmp_path / "tall_video_cover.png", (560, 1400), (40, 90, 160))
    card = PreviewCard(
        platform_name="tieba",
        platform_display_name="百度贴吧",
        title="竖版视频封面完整显示",
        cover_path=tall,
        card_type="video",
    )

    ready_card = asyncio.run(render_runtime.ensure_card_assets(card))
    context = asyncio.run(prepare_context(ready_card))
    template_card = context["card"]
    assert isinstance(template_card, TemplateCard)
    with _decode_data_uri(template_card.hero_uri) as image:
        assert image.width == 1000
        assert image.height == 2500

    _, _, environment = render_runtime.load_skin_pack("default")
    html = environment.get_template("video.html").render(**context)
    assert ".hero-img{display:block;width:1000px;height:auto;object-fit:contain}" in html


def test_two_content_images_stack_without_cropping(tmp_path: Path) -> None:
    first = _image(tmp_path / "first.png", (560, 700), (40, 90, 160))
    second = _image(tmp_path / "second.png", (560, 1400), (120, 60, 180))
    card = PreviewCard(
        platform_name="tieba",
        platform_display_name="百度贴吧",
        title="多图完整显示",
        blocks=(ImageBlock(first), ImageBlock(second)),
    )

    ready_card = asyncio.run(render_runtime.ensure_card_assets(card))
    context = asyncio.run(prepare_context(ready_card))
    _, _, environment = render_runtime.load_skin_pack("default")
    html = environment.get_template("common.html").render(**context)

    assert 'class="stacked-images"' in html
    assert 'height="1550"' in html
    assert 'height="3100"' in html
    assert '<div class="grid ' not in html


def test_helpers_round_rect(tmp_path: Path) -> None:
    source_path = _image(tmp_path / "rect_src.png", (200, 200), (255, 0, 0))
    source = helpers.read_rgba(source_path)
    result = helpers.round_rect(source, (100, 100), radius=20)
    assert result.size == (100, 100)
    # 检查左上角是否完全透明
    assert result.getpixel((0, 0))[3] == 0
    # 检查中心是否为不透明红色
    center_pixel = result.getpixel((50, 50))
    assert center_pixel[0] == 255
    assert center_pixel[3] == 255


def test_render_custom_skin_with_jinja_and_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pack_root = tmp_path / "pack"
    custom_skin_dir = pack_root / "custom_test"
    custom_skin_dir.mkdir(parents=True)

    manifest = {
        "name": "custom_test",
        "display_name": "自定义测试皮肤",
        "templates": {
            "default": "default.html",
            "common": "common.html",
            "video": "video.html",
        },
    }
    (custom_skin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (custom_skin_dir / "default.html").write_text(
        "<!doctype html><html><body><p>fallback</p></body></html>",
        encoding="utf-8",
    )

    context_code = """from __future__ import annotations
from html import escape
from ParserUID.render import helpers
from ParserUID.render.models import PreviewCard

def prepare_context(card: PreviewCard) -> dict[str, str]:
    if card.author and card.author.avatar_path:
        img = helpers.read_rgba(card.author.avatar_path)
        rounded = helpers.round_rect(img, (80, 80), radius=16)
        avatar_uri = helpers.data_uri(rounded)
    else:
        avatar_uri = ""
    return {
        "avatar_uri": avatar_uri,
        "title": card.title,
        "text": card.text,
        "safe_title": escape(card.title),
    }
"""
    template_code = """<!doctype html><html><body>
    <div class="custom-card">
        {% if avatar_uri %}<img src="{{ avatar_uri }}" alt="">{% endif %}
        <h1>{{ title }}</h1>
        <p>{{ text }}</p>
    </div>
    </body></html>
"""
    (custom_skin_dir / "common.html").write_text(template_code, encoding="utf-8")
    (custom_skin_dir / "context.py").write_text(context_code, encoding="utf-8")
    (custom_skin_dir / "__init__.py").write_text("", encoding="utf-8")
    (pack_root / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(render_runtime, "PACK_DIR", pack_root)

    import sys

    sys.path.insert(0, str(tmp_path))
    try:
        card = _card(tmp_path)
        path = asyncio.run(render_preview(card, skin="custom_test"))
        _assert_png(path)
    finally:
        sys.path.remove(str(tmp_path))


def test_render_unknown_skin_raises(tmp_path: Path) -> None:
    card = _card(tmp_path)
    with pytest.raises(ValueError, match="不支持的皮肤"):
        asyncio.run(render_preview(card, skin="non_existent_skin"))


def test_manifest_rejects_template_outside_pack(tmp_path: Path) -> None:
    pack_dir = tmp_path / "unsafe_skin"
    pack_dir.mkdir()
    manifest = {
        "name": "unsafe_skin",
        "display_name": "不安全皮肤",
        "templates": {"default": "../outside.html"},
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="目录内的 .html"):
        render_runtime.load_skin_manifest(pack_dir)
