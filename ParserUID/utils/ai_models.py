"""解析器向 AI Core 声明工具时使用的纯数据契约。"""

from __future__ import annotations

from typing import Protocol, Sequence
from dataclasses import dataclass
from collections.abc import Callable, Awaitable


class AiToolVisibilityContext(Protocol):
    """`visible_when` 可读取的最小上下文，与 GsCore RunContext 结构兼容。"""

    @property
    def deps(self) -> object | None: ...

    @property
    def messages(self) -> Sequence[object]: ...


AiToolVisibility = Callable[[AiToolVisibilityContext], bool | Awaitable[bool]]
AiToolFunc = Callable[..., Awaitable[str]]


@dataclass(frozen=True, slots=True)
class AiToolSpec:
    """解析器声明的单个 AI 工具；注册由插件统一完成。"""

    func: AiToolFunc
    category: str = "common"
    capability_domain: str | None = None
    covers: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    timeout: float | None = 90.0
    visible_when: AiToolVisibility | None = None

    @property
    def name(self) -> str:
        return self.func.__name__
