# 解析器包编写参考

## 最小包结构

内置解析器示例：

```text
ParserUID/parsers/example/
├── __init__.py
├── config.json
├── parser.py
└── models.py
```

`__init__.py`：

```python
from .parser import ExampleParser

PARSER = ExampleParser
```

`models.py`：

```python
from __future__ import annotations
from msgspec import Struct


class ExampleAuthor(Struct):
    name: str = ""
    uid: str = ""
    avatar_url: str = ""


class ExamplePostInfo(Struct):
    title: str = ""
    text: str = ""
    author: ExampleAuthor = ExampleAuthor()
    cover_url: str = ""
    video_url: str = ""
```

`parser.py`：

```python
from __future__ import annotations

import re
from typing import ClassVar

from ...utils.exceptions import ParseException
from ...utils.models import MediaContent, Platform
from ..base import BaseParser, handle
from .models import ExamplePostInfo


class ExampleParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name="example",
        display_name="示例平台",
    )

    @handle("example.com/post/", r"example\.com/post/(?P<post_id>\d+)")
    async def _parse_post(self, searched: re.Match[str]) -> list[MediaContent]:
        post_id = searched.group("post_id")
        client = await self.get_client()
        resp = await client.get(f"https://api.example.com/post/{post_id}")
        if resp.status_code != 200:
            raise ParseException(f"获取作品失败：HTTP {resp.status_code}")

        import msgspec
        info = msgspec.json.decode(resp.content, type=ExamplePostInfo)
        author = self.create_author(name=info.author.name, uid=info.author.uid, avatar_url=info.author.avatar_url)

        await self.send_info(
            title=info.title,
            card_type="video",
            author=author,
            text=info.text,
            url=f"https://example.com/post/{post_id}",
            cover_url=info.cover_url,
        )

        video = self.create_video_content(info.video_url)
        return self.result([video])
```

## `__init__.py` 规则

- 从同包模块导入解析器类，并导出 `PARSER`。
- 不在包入口发起网络请求、写文件或加载凭据。
- 不在包入口启动浏览器、调度器或长生命周期任务。
- 不逐个导入包内所有模块作为注册手段。

## 解析器包内命令（`__init__.py`）

解析器需要自己的登录、刷新、切换账号或维护操作时，直接在解析器包已有的
`__init__.py` 中注册命令。注册中心只导入该入口文件；解析器未启用也会注册命令，
但不会实例化解析器。

最小结构：

```text
ParserUID/parsers/example/
├── __init__.py
├── config.json
└── parser.py
```

内置解析器的 `__init__.py` 必须使用相对导入：

```python
from __future__ import annotations

import asyncio

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

from .auth import ExampleAuthError
from .parser import ExampleParser
from ...utils.exceptions import sanitize_error_text

PARSER = ExampleParser

sv = SV("示例平台解析器命令", pm=0, area="ALL")
_login_lock = asyncio.Lock()


@sv.on_fullmatch("example_login", block=True)
async def login(bot: Bot, ev: Event) -> None:
    """登录示例账号。"""
    del ev
    async with _login_lock:
        parser = await ExampleParser.active_instance()
        owns_parser = parser is None
        if parser is None:
            parser = ExampleParser()
        try:
            await bot.send("请完成登录")
            await parser.login_with_qrcode()
        except (ExampleAuthError, OSError) as exc:
            detail = sanitize_error_text(str(exc), hide_local_paths=True)
            await bot.send(f"登录失败：{detail}")
        finally:
            if owns_parser:
                await parser.close()
```

### 发现与导入

- 命令写在解析器包已有的 `__init__.py`，与 `PARSER` 一起导出。
- 不要创建字面的 `init.py`。它不会作为解析器入口被加载，且违反项目结构约定。
- 注册器只从解析器包的直接目录读取 `__init__.py`，不接受用户提供的任意文件路径。
- 第三方解析器允许按加载器提供的模块命名空间使用兼容导入方式；内置解析器必须遵守
  相对导入红线。
- `__init__.py` 导入失败时拒绝该解析器并记录错误；该失败不应让其他解析器无法注册。
  导入成功只代表 `PARSER` 和触发器已注册，不代表命令一定可用。
- 不要从 `parsers/__init__.py`、`parsers/base.py`、`parser.py` 或插件全局配置中
  手工导入平台命令模块。

### 触发器与命令约定

- 管理类命令优先使用 `on_fullmatch`；需要参数时用 `on_command` 或 `on_prefix`。
- 命令关键词应带平台语义，避免和普通聊天、其他插件命令冲突；平台解析器内部发现
  机制不负责全局命令名冲突仲裁，注册前必须搜索现有命令。
- 显式声明权限：登录、Cookie、Token、账号、凭据清理等命令使用 `pm=0`；确需群管理
  操作时按 GsCore 的 `pm` 语义设计，不得默认 `pm=6` 放开给普通用户。
- 显式声明 `area`（`GROUP` / `DIRECT` / `ALL`）和必要的 `block`。不能依赖其他插件的
  触发器阻断本命令。
- 命令处理函数签名固定为 `async def handler(bot: Bot, ev: Event) -> None`。使用 `Bot`
  和 `Event`，不直接依赖 `_Bot`。
- 命令关键词会受插件 `Plugins(prefix=..., allow_empty_prefix=...)` 影响：注册后
  触发器键可能带前缀（例如 `blogin` 变为 `psrblogin`）。验证时按后缀匹配，
  不要假设裸命令名；用户实际触发方式以当前插件前缀配置为准。
- 命令的直接反馈使用 `bot.send()`；这是命令模块允许直接发送的边界，不得把
  `bot.send()` 带进 `parser.py` 的自动解析处理函数。
- 自动链接解析仍只走 `parser.py` 中的 `@handle` 和唯一 `on_parser_message` 入口。
  不得为同一个链接再注册命令触发器，也不得让命令入口接管普通聊天。

### 取得解析器实例

需要实例的配置、会话或下载器时，优先复用注册表中的启用实例：

```python
parser = await ExampleParser.active_instance()
if parser is None:
    parser = ExampleParser()
    owns_parser = True
else:
    owns_parser = False
```

- `active_instance()` 返回注册表中的实例；解析器停用时返回 `None`。
- 临时实例只用于命令自身的操作，不得把它注册进快照，也不得修改其他解析器的状态。
- 临时实例创建的 HTTP 会话、下载器、临时文件和后台任务必须在 `finally` 中通过
  `await parser.close()` 释放。
- 复用实例时不要关闭注册表持有的会话。命令结束后只释放自己创建的资源。
- 解析器构造阶段不得发起网络请求；所有 I/O 放在命令处理或解析处理函数中。

### 登录、凭证与续期

- 命令可以完成登录、刷新和账号操作，但必须把用户配置写到
  `data/ParserUID/parser_config/<name>.json`，把 Cookie、Token 或扫码凭证写到
  `data/ParserUID/cookies/`。
- 凭证文件使用原子替换保存，并尽量限制为仅当前用户可读写；不得写源码目录或提交到
  仓库。
- 用户配置必须通过解析器配置模型读写，不能直接改全局 `config.json`，也不能在命令
  与解析器之间维护两份启用状态。
- 登录成功后要同时更新当前解析器实例的内存 Cookie 和持久化凭证；否则网页配置页、
  自动解析和续期会读到不同状态。
- 凭证续期使用已保存的 refresh token，串行化刷新操作，避免多个请求重复消耗同一个
  refresh token。续期失败只记录安全上下文，不把 Token 或完整响应返回给用户。
- 扫码轮询必须有总时长、二维码过期状态和用户确认提示；过期后要求重新生成，不得
  无限轮询。

### 命令中的异步与生命周期

- 命令内网络 I/O、扫码轮询、刷新和文件读写都必须异步，或通过
  `asyncio.to_thread()` 执行。
- 需要等待用户扫码时可以使用 `asyncio.Lock` 串行化同一账号的登录流程；不要创建
  无引用、无回收的后台轮询任务。
- 长时间任务必须受 Core 插件关闭路径管理，或在 `finally` 中取消并等待。
- 命令失败时向用户发送简短原因；日志记录解析器名、阶段和异常类型，不记录凭据、
  签名、完整响应体或带敏感查询参数的 URL。
- 不把命令测试数据、抓包凭证和本地截图写成生产默认值。

### 命令验证

至少离线验证：

1. 临时解析器目录中的 `__init__.py` 能被注册器执行，命令触发器出现在对应 `SV` 中。
2. 解析器停用时命令仍可触发，且不会意外创建或泄漏解析器实例。
3. 命令失败时只发送安全提示，成功时只写数据目录中的凭证和用户配置。
4. 登录、刷新和关闭路径都有明确的资源回收。

## 解析器帮助与命令图标（`help.json` 与 `icon_path/`）

解析器在自己的包内注册专属命令后，应通过 `help.json` 与 `icon_path/` 向帮助系统提供说明和图标。
插件帮助系统会在启动初始化与出图时自动扫描所有已注册解析器，并将命令聚合拼接到总帮助图上。

### 1. 职责划分与生成契约

- **解析器只提供 `data` 命令项**：解析器只需维护自身命令的名称、用法、示例和权限。
- **标题和描述由插件统一配置**：杜绝各平台编写风格不一的分类名。帮助聚合层统一读取各解析器 `config.json` 的 `display_name`：
  - 分类标题：`f"{manifest.display_name}解析命令"`（例如 `网易云音乐解析命令`、`酷狗音乐解析命令`）
  - 分类描述：`f"由 {manifest.display_name} 解析器提供的命令"`
- 解析器 `help.json` 中**禁止**且**无需**提供自定义分类名或顶层 `desc`。

### 2. `help.json` 数据规范

文件路径：`ParserUID/parsers/<name>/help.json`（内置）或 `data/ParserUID/thirdparty/parsers/<name>/help.json`（第三方）。

结构模板：

```json
{
  "data": [
    {
      "name": "酷狗登录",
      "desc": "通过酷狗官方二维码登录普通版",
      "eg": "酷狗登录",
      "highlight": 6,
      "need_ck": false,
      "need_sk": false,
      "need_admin": false
    },
    {
      "name": "酷狗状态",
      "desc": "查看凭据状态及当前 API 模式",
      "eg": "酷狗状态",
      "highlight": 6,
      "need_ck": false,
      "need_sk": false,
      "need_admin": false
    }
  ]
}
```

字段约束：

| 字段 | 类型 | 必填 | 说明与缺省行为 |
|------|------|------|----------------|
| `name` | `str` | 是 | 命令名称，必须为非空字符串，建议与 `__init__.py` 中注册的命令一致 |
| `desc` | `str` | 否 | 命令功能描述，缺省时回退为 `name` |
| `eg` | `str` | 否 | 触发示例，缺省时回退为 `name` |
| `highlight` | `int` | 否 | 帮助条目高亮等级，缺省为 `6`（普通） |
| `need_ck` | `bool` | 否 | 是否依赖 Cookie，缺省为 `false` |
| `need_sk` | `bool` | 否 | 是否依赖 SecretKey，缺省为 `false` |
| `need_admin` | `bool` | 否 | 是否需要管理员权限，缺省为 `false`。若为 `true`，普通用户（`pm > 1`）触发帮助时系统自动过滤该条目 |

### 3. `icon_path/` 图标目录与冲突规则

解析器可在自己的包目录下创建 `icon_path/` 目录，放置以命令名命名的 PNG 图标（例如 `酷狗登录.png`）。

- **严禁提供 `通用.png`（绝对红线）**：
  - `通用.png` 是整个插件在未匹配到专属图标时的全局兜底图标，严格且唯一由插件核心提供（`ParserUID/help/icon_path/通用.png`）。
  - 解析器包内若包含 `通用.png`，在聚合时会被强制跳过并打印告警日志：`[Parser·帮助] 解析器 {name} 提供了被禁止的 '通用.png'，已被忽略`。
- **冲突后覆盖规则**：
  - 如果多个解析器提供了同名的图标文件，以后扫描/后加载的覆盖先加载的。
  - 扫描顺序固定为：插件核心图标 -> 内置解析器（按目录字母序） -> 第三方解析器（按目录字母序，拥有最高覆盖权）。
- **聚合落盘机制（方案 3.1 清空重建）**：
  - GsCore 的帮助图绘制引擎底层只接受一个单一 `icon_path: Path` 目录参数并执行 `.glob("*.png")`。
  - 插件在启动与帮助图生成时，通过内存有序字典（Single Source of Truth）决出最终胜出的源文件，清空并重建同步到 `data/ParserUID/icon_path/`。
  - 解析器**不得直接操作** `data/ParserUID/icon_path/` 或插件核心目录，只需将自身图标静态放置在解析器包内的 `icon_path/` 中。

### 4. 完整示例

以酷狗音乐解析器为例：

```text
ParserUID/parsers/kugou/
├── __init__.py           # 注册 酷狗登录、酷狗概念版登录、酷狗状态
├── config.json           # display_name 为 "酷狗音乐"
├── help.json             # {"data": [{"name": "酷狗登录", ...}, ...]}
├── icon_path/            # 图标目录
│   ├── 酷狗登录.png
│   ├── 酷狗概念版登录.png
│   └── 酷狗状态.png
└── parser.py             # 链接解析
```

生成的帮助图效果：
- 分类标题自动显示为：`酷狗音乐解析命令`
- 分类描述自动显示为：`由 酷狗音乐 解析器提供的命令`
- 命令列表与图标自动按配置渲染在分类下方。

## `@handle` 规则

- 第一个参数是低成本预筛选关键词。
- 第二个参数是正则表达式，匹配明确 host/path。
- 优先使用命名分组，通过 `searched.group("...")` 读取。
- 不使用 `.*` 或宽泛 `https?://` 规则。
- 同一平台有多种明确链接形式时，可注册多个处理函数。
- 关键词在所有内置和第三方解析器中唯一。
- 重复关键词必须拒绝注册，不得静默覆盖。

## 处理方法

处理方法必须是异步函数，接收 `re.Match[str]`。标准顺序：

1. 从正则分组取平台 ID。
2. 请求平台 API 或解析页面（普通用 `get_client()`，防爬用 `get_curl_client()`）。
3. 读取凭据：`cookie = self.get_config_value("cookie")`。
4. 将响应使用 `msgspec.json.decode` 转换为平台专属 `msgspec.Struct` 模型（严禁 `Any`、`cast`、`type: ignore`）。
5. 构造描述数据，调用统一接口 `await self.send_info(card_type=..., ...)`。
6. 使用 `create_video_content` / `create_image_contents` 构造延迟下载媒体项。
7. 返回按发送顺序排列的内容列表。

## 媒体内容构造方法

优先使用基类提供的工厂方法：

- `create_author(name=..., avatar_url=..., ...)`
- `create_image_contents(urls=...)`
- `create_video_content(url_or_path=...)`
- `create_live_photo_content(video_url=..., image_url=...)`
- `create_dynamic_contents(urls=...)`
- `create_audio_content(...)`
- `create_file_content(...)`
- `create_text_content(...)`

解析阶段不得调用 `get_path()` 或等价的强制下载方法。
媒体将在发送层消费内容列表时按需下载。
