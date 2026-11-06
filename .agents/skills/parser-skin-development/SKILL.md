---
name: parser-skin-development
description: >
  当用户要求为 ParserUID 创建、修改、审查或调试卡片皮肤包时触发。
  适用于新增 render/pack/name/、编写 manifest.json、Jinja HTML 与可选
  context.py、使用相对导入的 render.helpers 处理图片、接入 PreviewCard，
  以及排查皮肤渲染、缓存和测试问题。不要用于新增平台解析器，也不要用于
  修改 GsCore 核心框架。
---

# ParserUID 卡片皮肤开发

本 Skill 只展开 ParserUID 卡片皮肤包。开始前先读项目根目录 `AGENTS.md`；
项目总规范、GsCore 架构和解析器开发不在本 Skill 范围内。

## 快速导航

| 任务 | 文档 |
|------|------|
| 新建皮肤包、写 manifest 和 Jinja 模板 | [references/pack-contract.md](references/pack-contract.md) |
| 使用 PreviewCard、context.py、helpers 和 Takumi | [references/authoring.md](references/authoring.md) |
| 渲染测试、缓存和交付前检查 | [references/checklist.md](references/checklist.md) |

## 当前实际链路

`render_preview(card, skin=...)` 的调用顺序固定为：

1. 按内容摘要检查渲染缓存。
2. `runtime.ensure_card_assets(card)` 下载远程封面、头像和正文图片。
3. `render.registry.resolve_skin_pack(skin)` 定位内置或第三方皮肤包。
4. 若存在 `context.py`，调用 `prepare_context(card)` 生成 Jinja 上下文。
5. 按 manifest 顺序选择 HTML 模板并渲染。
6. `pytakumi` 出图，并按内容摘要缓存 PNG。

内置皮肤从 `ParserUID/render/pack/<name>/` 加载，第三方皮肤从
`data/ParserUID/addons/skins/<name>/` 加载。网页配置页会展示
来源和版本，切换第三方皮肤前要求管理员确认。内置与第三方同名时保留内置，
第三方包会被拒绝。

## 皮肤契约

最少目录：

```text
ParserUID/render/pack/<name>/
├── manifest.json
├── default.html
├── common.html
├── video.html
└── context.py          # 可选
```

`manifest.json` 至少声明 `name`、`display_name`、`version` 和 `templates`。
`templates` 必须包含 `default`，可包含 `common`、`video` 和平台专属键。
目录名和 `name` 必须一致；模板名只能是皮肤目录内的相对 `.html` 路径。

推荐入口是 Jinja 模板本身：

```text
default: default.html
common:  common.html
video:   video.html
```

如果需要 PIL 加工，增加 `context.py` 并导出 `prepare_context(card)`，返回
Jinja 上下文映射。没有 `context.py` 时，模板上下文默认只有 `card`。
禁止在 Python 中拼接完整 HTML，也禁止导出 `build_card_html()` 这类返回
HTML 的旧入口。

平台专属模板按 `<platform>.html` 命名，并在 `templates` 注册后再声明。选择
顺序固定为平台专属模板、当前类型模板、`default`。

## 素材约定

- `ensure_card_assets()` 已把可下载的远程素材落到本地；皮肤只使用
  `cover_path`、`avatar_path`、`blocks[*].path` 等本地字段。
- 所有路径字段都可能为 `None`，图片也可能损坏；先判断 `Path.is_file()`，
  缺失时使用纯 CSS 占位或 `helpers.empty_image_placeholder()`。
- 所有正文和属性值都写在 Jinja 的 `{{ value }}` 中，由 autoescape 处理；
  不要手工拼接 HTML，也不要随意使用 `|safe`。
- 不用外部 CDN、远程字体、远程图片或任意本地路径。需要图片时使用
  `helpers.data_uri()` 生成 base64 data URI。

## 视觉约束

- 字体优先写 `MiSans`，可保留中文回退字体。不要依赖 Takumi 未注册的字体。
- 普通图文卡片按 620 CSS 逻辑像素设计，输出宽度为 1000；视频卡片使用
  1000 逻辑宽度和 96 DPI。
- `flex`、`grid`、圆角、`object-fit`、`box-shadow` 和简单 `filter: blur()`
  可用。避免依赖 `zoom`、`mask-image`、`mix-blend-mode`、复杂 SVG 和
  浏览器专属滤镜；需要渐隐、圆形或模糊背景时，优先用 helpers 预处理成
  RGBA 图片。
- 同一图片同时用于背景和大图时，分别生成和缓存不同尺寸，不要反复从模糊
  背景反推原图。

## 常用 helpers

皮肤从当前渲染包按层级相对导入：

```python
from ... import helpers
```

核心函数：

| 函数 | 用途 |
|------|------|
| `read_rgba(path)` | 读取图片、修正 EXIF、转为 RGBA |
| `fit_cover(image, size)` | 等比缩放并居中裁切 |
| `fit_contain(image, size)` | 等比缩放并放入透明画布 |
| `circle(image, size)` | 正圆裁切 |
| `round_rect(image, size, radius)` | 圆角矩形裁切 |
| `fade_vertical(image, top_ratio=..., bottom_ratio=...)` | 上下渐隐 |
| `ambient_background(image, size=..., blur=..., wash=...)` | 模糊氛围背景 |
| `grid_overlay(size, step)` | 叠加浅色网格 |
| `icon(name, color, size=64)` | 绘制内置图标 |
| `data_uri(image)` | 转为 PNG data URI |
| `empty_image_placeholder(size)` | 生成中性占位图 |

`icon()` 当前只接受 `eye`、`heart`、`comment`、`star`、`play`、`clock`、
`share`。不要猜未定义图标名。

## 验证

新增或修改皮肤后，用 `docs/output/` 保存本地素材与验证截图，调用
`render_preview(..., skin="<name>")` 检查 PNG 签名、输出宽度和图片比例。
验证 context.py 派生字段与模板回退顺序时，不要把结果写入插件源码目录。

在插件根目录运行：

```powershell
python -m ruff check ParserUID
python -m ruff format --check ParserUID
python -m basedpyright ParserUID
```

修改已有皮肤的视觉输出后，清理 `data/ParserUID/render_cache/` 或提升
`runtime._RENDER_VERSION`，否则内容寻址缓存可能继续返回旧图。
