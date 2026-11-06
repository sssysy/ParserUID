# 皮肤交付检查

## 结构

- [ ] 内置皮肤位于 `ParserUID/render/pack/<name>/`，第三方皮肤位于
      `data/ParserUID/addons/skins/<name>/`。
- [ ] `manifest.json` 是合法 JSON，`name` 与目录名一致，`templates.default` 存在。
- [ ] HTML 位于 Jinja 模板，入口是 `common.html` / `video.html` / `default.html`。
- [ ] `context.py` 只导出 `prepare_context(card)` 并返回映射，不返回 HTML。
- [ ] 没有依赖默认皮肤私有 `TemplateCard`，除非明确记录耦合原因。
- [ ] 没有读取任意路径、访问网络、执行外部脚本或直接发送消息。

## 数据与图片

- [ ] `cover_path`、`avatar_path`、`block.path` 为 `None` 时不会崩溃。
- [ ] 缺失图片有 CSS 占位或 `empty_image_placeholder()`。
- [ ] 文本和属性通过 Jinja autoescape 输出，没有随意使用 `|safe`。
- [ ] 图片使用 `helpers.data_uri()`，透明图保持 PNG/RGBA。
- [ ] 正文块保持原顺序，没有按媒体类型重排。
- [ ] 圆角、圆形、渐隐和模糊优先用 helpers 预处理。

## Takumi

- [ ] 普通卡片按 620 逻辑宽度设计，视频卡片按 1000 逻辑宽度设计。
- [ ] 不依赖 `zoom`、`mask-image`、`mix-blend-mode` 或不稳定 SVG。
- [ ] 字体优先 `MiSans`，没有依赖未注册的远程字体。
- [ ] 没有外部 CDN、远程图片或 `file://` 路径。

## 验证

- [ ] 用 `docs/output/` 中的本地卡片和图片做离线检查。
- [ ] 调用 `render_preview(card, skin="<name>")` 并检查 PNG 签名与宽度。
- [ ] 平台专属模板、类型模板和 `default` 的逐级回退顺序经过验证。
- [ ] 覆盖无封面、无头像、空评论、图片块缺失等空值场景。
- [ ] 未知皮肤名抛出明确异常。
- [ ] 修改已有皮肤后清理 `data/ParserUID/render_cache/` 或提升
      `runtime._RENDER_VERSION`。

## 交付命令

在插件根目录运行：

```powershell
python -m ruff check ParserUID
python -m ruff format --check ParserUID
python -m basedpyright ParserUID
```

如果新增或修改了皮肤渲染路径，用 `docs/output/` 下的本地素材执行一次出图检查。
