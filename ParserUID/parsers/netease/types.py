"""网易云解析器的内部枚举与结果类型。"""

from __future__ import annotations

from typing import Final, Literal
from dataclasses import dataclass

QualityLevel = Literal[
    "standard",
    "higher",
    "exhigh",
    "lossless",
    "hires",
    "jyeffect",
    "dolby",
    "vivid",
    "jymaster",
    "sky",
]
UrlSource = Literal["download", "player"]

QUALITY_ORDER: Final[tuple[QualityLevel, ...]] = (
    "jymaster",
    "hires",
    "lossless",
    "exhigh",
    "higher",
    "standard",
    "jyeffect",
    "dolby",
    "vivid",
    "sky",
)
QUALITY_LABELS: Final[dict[QualityLevel, str]] = {
    "standard": "标准",
    "higher": "较高",
    "exhigh": "极高",
    "lossless": "无损",
    "hires": "Hi-Res",
    "jyeffect": "高清臻音",
    "dolby": "杜比全景声",
    "vivid": "臻音全景声",
    "jymaster": "超清母带",
    "sky": "沉浸环绕声",
}


@dataclass(frozen=True, slots=True)
class ResolvedAudio:
    url: str
    level: QualityLevel
    br: int
    extension: str
    source: UrlSource
    trial_seconds: int = 0

    @property
    def display_quality(self) -> str:
        label = QUALITY_LABELS[self.level]
        if self.br > 0:
            return f"{label} · {self.br // 1000} kbps"
        return label


def normalize_level(value: str) -> QualityLevel | None:
    match value:
        case (
            "standard"
            | "higher"
            | "exhigh"
            | "lossless"
            | "hires"
            | "jyeffect"
            | "dolby"
            | "vivid"
            | "jymaster"
            | "sky"
        ):
            return value
        case _:
            return None


def levels_from(preferred: QualityLevel) -> tuple[QualityLevel, ...]:
    """从偏好档开始逐级降档，保留请求顺序。"""
    start = QUALITY_ORDER.index(preferred)
    return QUALITY_ORDER[start:]
