# 皮肤编写参考

## PreviewCard 数据

皮肤接收 `ParserUID.render.models.PreviewCard`。常用字段：

| 字段 | 含义 |
|------|------|
| `platform_name` / `platform_display_name` | 平台标识与展示名 |
| `title` / `text` | 标题与正文 |
| `author` | `Author` 或 `None` |
| `timestamp` / `source_url` | 时间与来源 |
| `cover_path` / `cover_url` | 封面本地路径或远程地址 |
| `blocks` | 按原顺序排列的 `TextBlock` / `ImageBlock` |
| `metrics` | 卡片指标 |
| `comments` | 评论与一级回复 |
| `repost` | 可选的转发卡片 |
| `video_duration` / `video_quality` | 视频信息 |
| `is_video` | 是否使用视频版式 |

`block.path`、`cover_path`、`avatar_path` 都可能为 `None`。不要假设
`blocks` 只有图片，也不要把文本块和图片块重新排序。

## 建议的接收顺序

1. 读取字段并用 `isinstance` 区分 `TextBlock` / `ImageBlock`。
2. 只读取非空且 `is_file()` 的本地图片路径。
3. 需要裁切、圆角、渐隐或模糊时，在 `context.py` 中用 helpers 处理。
4. 用 `data_uri()` 把图片交给 Jinja 上下文。
5. 文本只写 `{{ value }}`，由 autoescape 处理；不要手工拼接 HTML。
6. 缺失图片时输出 CSS 占位块或 `empty_image_placeholder()`。

## 安全 Jinja 写法

```html
<h1>{{ card.title }}</h1>
<p class="copy">{{ card.text }}</p>
<img src="{{ avatar_uri }}" alt="">
```

Jinja 环境已启用 `select_autoescape(("html", "xml"))`，不要为了省事使用
`|safe`。只有确认内容是本插件生成的安全标记时才可例外。

- 不要引用外部 CSS、字体、脚本或 CDN。
- 不要把 `file://` 路径写进 HTML；图片走 data URI。

## context.py 与图片处理

```python
from collections.abc import Mapping

from ParserUID.render import helpers
from ParserUID.render.models import PreviewCard


def _avatar_uri(card: PreviewCard) -> str:
    author = card.author
    if author is None or author.avatar_path is None:
        return helpers.empty_image_placeholder((96, 96))
    if not author.avatar_path.is_file():
        return helpers.empty_image_placeholder((96, 96))
    image = helpers.read_rgba(author.avatar_path)
    return helpers.data_uri(helpers.round_rect(image, (96, 96), radius=18))


def prepare_context(card: PreviewCard) -> Mapping[str, object]:
    """HTML 由 Jinja 模板负责，这里只提供派生数据。"""
    return {"card": card, "avatar_uri": _avatar_uri(card)}
```

`read_rgba()` 会修正 EXIF 方向、限制源文件大小并把最长边缩到 2400 像素。
处理后的图片保持 RGBA；不要转成 JPEG，否则透明边缘会变黑。

常用组合：

- 圆形头像：`helpers.circle(image, 120)`
- 圆角方形头像：`helpers.round_rect(image, (120, 120), 20)`
- 封面完整显示：`helpers.fit_contain(image, (1000, 560))`
- 封面裁切：`helpers.fit_cover(image, (1000, 560))`
- 上下渐隐：`helpers.fade_vertical(image, top_ratio=0.08, bottom_ratio=0.18)`
- 模糊氛围背景：`helpers.ambient_background(image, size=(1000, 1600), blur=40, wash=(8, 10, 16, 150))`

## Takumi 与 CSS

当前渲染器通过 `pytakumi` 出图，不能当作完整 Chromium 使用。

- 普通图文按 620 CSS 逻辑宽度设计，最终输出 1000 像素宽；视频按 1000
  逻辑宽度、96 DPI 设计。
- `display:flex`、`display:grid`、`object-fit:cover`、圆角、阴影、
  `filter: blur()` 和普通渐变可用。
- `zoom`、`mask-image`、`backdrop-filter`、`mix-blend-mode` 和依赖浏览器
  字体回退的写法不稳定，优先改成预处理图片或绝对定位渐变层。
- 内联 SVG 的尺寸和形状在不同写法下可能异常；需要固定图标时使用
  `helpers.icon()` 生成的 PNG data URI。
- 图片渐变优先写入 RGBA，不要只在 CSS 里写 `mask-image`。

## 完整骨架

`context.py`：

```python
from collections.abc import Mapping

from ParserUID.render import helpers
from ParserUID.render.models import ImageBlock, PreviewCard, TextBlock
from ParserUID.render.template_data import CARD_FOOTER


def _avatar(card: PreviewCard) -> str:
    author = card.author
    if author is None or author.avatar_path is None or not author.avatar_path.is_file():
        return helpers.empty_image_placeholder((96, 96))
    image = helpers.read_rgba(author.avatar_path)
    return helpers.data_uri(helpers.round_rect(image, (96, 96), 18))


def _cover(card: PreviewCard) -> str:
    if card.cover_path is None or not card.cover_path.is_file():
        return ""
    image = helpers.read_rgba(card.cover_path)
    return helpers.data_uri(helpers.fit_cover(image, (1000, 560)))


def _blocks(card: PreviewCard) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for block in card.blocks:
        if isinstance(block, TextBlock):
            blocks.append({"kind": "text", "text": block.text})
        elif isinstance(block, ImageBlock) and block.path is not None and block.path.is_file():
            image = helpers.read_rgba(block.path)
            uri = helpers.data_uri(helpers.fit_cover(image, (1000, 1000)))
            blocks.append({"kind": "image", "uri": uri})
    return blocks


def prepare_context(card: PreviewCard) -> Mapping[str, object]:
    """构建最小可用的自定义皮肤上下文。"""
    return {
        "card": card,
        "avatar_uri": _avatar(card),
        "cover_uri": _cover(card),
        "blocks": _blocks(card),
        "footer": CARD_FOOTER,
    }
```

`common.html`：

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
*{box-sizing:border-box}
body{margin:0;background:#101318;color:#f5f7fa;font-family:MiSans,sans-serif}
.card{width:620px;padding:32px;background:#171b22}
.cover{width:100%;display:block;border-radius:16px}
.content-image{width:100%;display:block;margin-top:16px;border-radius:12px}
.title{margin-top:20px;font-size:28px;line-height:1.3}
</style>
</head>
<body>
<main class="card">
    {% if cover_uri %}<img class="cover" src="{{ cover_uri }}" alt="">{% endif %}
    <img class="avatar" src="{{ avatar_uri }}" alt="">
    <h1 class="title">{{ card.title }}</h1>
    <p>{{ card.text }}</p>
    {% for block in blocks %}
        {% if block.kind == "text" %}
            <p>{{ block.text }}</p>
        {% else %}
            <img class="content-image" src="{{ block.uri }}" alt="">
        {% endif %}
    {% endfor %}
    <p>{{ footer }}</p>
</main>
</body>
</html>
```
