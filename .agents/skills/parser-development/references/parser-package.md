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
