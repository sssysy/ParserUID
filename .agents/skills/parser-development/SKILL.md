---
name: parser-development
description: >
  当用户要求为 ParserUID 新增、重写、审查或调试平台解析器时触发。
  适用于创建 parsers/<name>/ 包、编写 @handle 链接匹配、实现平台 API 请求、
  构造 Description 与有序媒体结果、调整解析器 config.json、排查注册冲突、
  处理第三方解析器目录，以及修改解析器相关测试。
---

# ParserUID 平台解析器开发

本 Skill 面向 ParserUID 的平台解析器开发。修改前先读项目根目录 `AGENTS.md`；
本 Skill 只展开解析器包、配置和验收细节，不替代项目总规范。

## 快速导航

| 任务 | 文档 |
|------|------|
| 新增或重写解析器包、实现 `BaseParser` 与 `@handle` | [references/parser-package.md](references/parser-package.md) |
| 修改 `config.json`、用户配置和网页配置页字段 | [references/configuration.md](references/configuration.md) |
| 提交前检查注册、解析、发送、安全和测试 | [references/checklist.md](references/checklist.md) |

## 固定架构

ParserUID 是 GsCore 插件，不直接依赖 AstrBot 运行时。

内置解析器目录：

```text
ParserUID/parsers/<name>/
├── __init__.py       # 导出 PARSER
├── config.json       # 默认值和字段定义
├── parser.py         # BaseParser 子类与 @handle 入口
├── models.py         # 平台专属 msgspec.Struct 数据模型
└── <module>.py       # 平台私有请求与解析逻辑
```

第三方解析器目录：

```text
data/ParserUID/thirdparty/parsers/<name>/
├── __init__.py
├── config.json
├── parser.py
├── models.py
└── <module>.py
```

目录名、`config.json.name`、`PARSER.platform.name` 必须完全一致。
注册中心只导入解析器包的 `__init__.py`，不逐个扫描包内模块。

新增平台时不得修改：

- `ParserUID/parsers/__init__.py`
- `ParserUID/utils/registry.py` 中的平台清单
- `ParserUID/parser.py` 中的平台分支
- 插件全局 `config.py` 或全局 `config.json` 来加入平台字段

## 解析器契约

每个解析器必须：

- 继承 `BaseParser`。
- 声明 `platform: ClassVar[Platform]`。
- 在 `__init__` 中调用 `super().__init__(...)`，不在构造阶段发起网络请求。
- 只通过 `@handle(keyword, pattern)` 注册链接入口。
- 关键词在所有内置和第三方解析器中唯一。
- 正则匹配明确 host 和 path，不用宽泛规则抢占其他平台。
- 使用基类注入的异步会话（`self.get_client()` 或 `self.get_curl_client()`）与下载器（`self.downloader`）。
- 凭据直接从专属配置项读取：`self.get_config_value("cookie")`，废弃独立 CookieJar。
- 先获取元信息并调用统一入口 `send_info(card_type="common"|"video", ...)` 发送预览卡片。
- 卡片发送完成后再启动最终媒体下载。
- 返回有序内容列表，列表顺序就是发送顺序。
- 通过基类的 `result()` 交给 `sender.py` 发送。
- 失败时抛出 `ParseException` 或其子类，不返回占位媒体。

解析器不得：

- 直接构造 GsCore `Message` 或 `MessageSegment`。
- 直接调用 `bot.send()`。
- 直接导入 `sender.py` 或 `render.py` 完成发送。
- 自行实现一套下载器、CookieJar、重试队列或私有缓存目录。
- 修改其他解析器的请求头、Cookie 或全局状态。

详细骨架与内容构造参见
[references/parser-package.md](references/parser-package.md)。

## 注册与匹配

启动或刷新快照时，注册流程固定为：

1. 扫描内置和第三方解析器目录。
2. 只读取含 `config.json` 和 `__init__.py` 的直接子目录。
3. 校验目录名、配置名和平台名三者一致。
4. 从 `PARSER` 类收集 `@handle` 的关键词和正则。
5. 检查关键词冲突；冲突时拒绝注册并记录两个平台。
6. 读取或创建 `data/ParserUID/parser_config/<name>.json`。
7. 只实例化已启用的解析器，生成只读注册快照。

匹配规则：

- 每条消息只处理第一个链接。
- 平台之间没有优先级。
- 首个命中的解析器接受任务后立即停止传播。
- 注册失败只跳过该解析器，不能让整个插件无法启动。
- 所有解析器默认不启用，`default_enabled` 必须为 `false`。

解析器匹配失败或任务已交给其他解析器后，不得继续尝试后续解析器。

## 发送与卡片规范

卡片发送统一通过 `self.send_info()`：
- 废弃割裂的 `send_desc_common` 与 `send_desc_video`，合并为单一入口。
- 通过 `card_type: Literal["common", "video"] = "common"` 参数显式指定卡片版式。
- 解析器只提供结构化元信息，不直接操作 HTML/CSS 或皮肤包。

`result()` 用于提交有序内容：
- 文本、图片、视频、音频、文件使用明确的公共内容类型。
- 复合内容支持 `LivePhotoContent`（实况照片合成）与 `DynamicContent`。
- 不按媒体类型重排，列表顺序就是发送顺序。
- 内容数量大于等于 `3` 时，由发送层默认使用合并转发。
- 目标适配器明确不支持合并转发时，由发送层回退为文字消息。

## 配置与 Cookie 凭证

- **无 CookieJar 架构**：彻底干掉旧版复杂 CookieJar。Cookie 直接作为解析器专属配置字段定义在解析器包内的 `config.json`。
- 凭证字段必须标记 `secret: true`。
- 用户配置持久化保存在：
  ```text
  data/ParserUID/parser_config/<name>.json
  ```
- 解析器运行时通过 `self.get_config_value("cookie")` 按需取用，转为 Header 或传给客户端。
- 凭据续期（如 B站 WBI、Token 刷新）由解析器内部模块自行计算，并通过更新配置写回用户数据目录，不得在源码目录留存状态。

## 网络、下载与 FFmpeg

- **网络引擎**：
  - 常规平台调用 `await self.get_client()` 使用 `httpx.AsyncClient`。
  - 需要绕过 Cloudflare / TLS 指纹反爬的平台（小红书、知乎、小黑盒、iwara 等）调用 `await self.get_curl_client()` 获取 `curl_cffi.requests.AsyncSession`。
  - UA 由各平台解析器在自己的包内按需定义私有常量，框架不硬编码全局 UA。
- **通用下载器**：
  - 统一调用 `self.downloader`（基于 `ParserUID.utils.downloader.Downloader`）。
  - 所有媒体文件统一落盘到插件数据缓存目录：`data/ParserUID/cache`。
  - 支持分块流式下载、断点续传、超时重试、大小上限拦截（`max_bytes`）与 URL SHA256 去重缓存。
  - 视频平台（YouTube、Twitter、TikTok 等）流媒体提取通过 `self.downloader.download_with_ytdlp` 异步执行。
- **FFmpeg 工具链**：
  - 音视频分离合并调用 `ParserUID.utils.ffmpeg.merge_av`。
  - 实况照片与首图合成调用 `ParserUID.utils.ffmpeg.merge_to_live_mp4`。
- **公共工具**：
  - 跨平台通用函数统一使用 `ParserUID.utils.utils`（如 `LimitedSizeDict`、`generate_file_name`、`fmt_size`、`format_num`、`extract_json_url`）。

## 代码规范与绝对红线

严格遵循项目根目录 `AGENTS.md` 红线要求：

1. **绝对禁止 `Any`**：
   - 运行时变量必须有完整、可追踪的类型。
   - 平台外部 API 响应**必须**使用 `msgspec.Struct`（或 dataclass）定义数据模型，放在平台包内的 `models.py`。
   - 对可能缺失的字段设置显式默认值（如 `str = ""`、`list[Item] = []`），避免类型不确定。
2. **绝对禁止 `cast()` 与 `type: ignore`**：
   - 使用类型守卫（`isinstance`）或联合类型（`A | B`）解决类型分流。
3. **绝对禁止 `getattr()` 与 `dict.get()` 宽泛取值**：
   - 依赖严格声明的 `msgspec.Struct` 属性，让类型检查器全程校验。
4. **禁止 `try-except` 兜底吞异常**：
   - 逻辑漏洞与类型错误必须从代码本身修复，仅外部不可信输入解析处允许按需保留。
5. **注释限制**：
   - `#` 注释最多两行，每行不超过 88 个字符，一句话点明为什么或有什么坑，不翻译代码。

## 验证

在插件根目录执行完整的交付闸验证，缺一不可：

```powershell
uv run --dev ruff check ParserUID tests
uv run --dev ruff format --check ParserUID tests
uv run --dev basedpyright ParserUID
uv run --dev pytest --basetemp=.pytest_tmp -o cache_dir=.pytest_cache tests -q
```
