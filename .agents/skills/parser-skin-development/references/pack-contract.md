# 皮肤包契约

## 目录

内置皮肤必须位于：

```text
ParserUID/render/pack/<name>/
├── manifest.json
├── default.html
├── common.html
├── video.html
├── context.py          # 可选：只返回 Jinja 上下文
└── assets/            # 可选，仅存放皮肤包自己的静态资源
```

`AGENTS.md` 约定的第三方皮肤目录是
`data/ParserUID/thirdparty/renderskinpacks/<name>/`。
`render.registry.scan_skin_packs()` 会同时扫描内置和第三方目录，网页配置页
只列出通过 manifest、目录名和模板文件校验的皮肤。内置与第三方同名时保留
内置，第三方包会被拒绝。

## manifest.json

示例：

```json
{
  "name": "example",
  "display_name": "示例皮肤",
  "version": "1.0.0",
  "templates": {
    "default": "default.html",
    "common": "common.html",
    "video": "video.html",
    "bilibili": "bilibili.html"
  }
}
```

约束：

- `name` 与目录名一致。
- `templates` 必须包含 `default`；值只能是皮肤目录内的 `.html` 相对路径。
- `common` 与 `video` 是版式键；`bilibili` 等平台键对应
  `<platform>.html`。
- 渲染顺序是平台模板、当前类型模板、`default`，不可用则继续回退。
- manifest 必须是合法 JSON。改完后用 `load_skin_manifest()` 读取校验。

## 模板入口

HTML 入口就是 Jinja 模板。`default.html` 是最低回退：

```html
<!doctype html>
<html lang="zh-CN">
<body>
    <main class="card">{{ card.title }}</main>
</body>
</html>
```

需要素材加工时，增加 `context.py`：

```python
from collections.abc import Mapping

from ...models import PreviewCard


def prepare_context(card: PreviewCard) -> Mapping[str, object]:
    """返回模板变量，不返回 HTML。"""
    return {"card": card, "title": card.title}
```

context.py 的返回值直接传给 `Template.render(**context)`。异步版本同样
支持，但皮肤不应访问网络。`context.py` 可以导入公共 helpers，不能导入
解析器包，也不能在 Python 中拼接完整 HTML。

第三方皮肤的 `context.py` 会在受控包命名空间中加载。推荐使用与内置皮肤
相同的相对导入：

```python
from ... import helpers
from ...models import PreviewCard
```

加载器不会把第三方目录加入全局 `sys.path`。

## 默认皮肤复用

`pack/default/pipeline.py` 中的 `prepare_template_card()`、`TemplateCard`、
`common.html` / `video.html` 是默认皮肤内部实现。第三方皮肤要复用这些
逻辑时，明确写出耦合原因，并确认默认皮肤改版不会破坏该皮肤；更推荐直接
用 `PreviewCard` 和公共 helpers。

## 缓存

缓存键包含卡片字段、原图内容摘要和皮肤名，不包含 HTML、CSS 或
`context.py` 的源码摘要。修改已有皮肤的视觉结果或上下文字段后：

1. 删除 `data/ParserUID/render_cache/`；或
2. 提升 `ParserUID/render/runtime.py` 的 `_RENDER_VERSION`。

不同皮肤使用不同 `skin` 参数，不会互相复用 PNG。
