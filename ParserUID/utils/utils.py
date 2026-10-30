"""跨平台解析与媒体处理通用工具函数。"""

from __future__ import annotations

import re
import json
import hashlib
from typing import TypeVar
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urlparse

K = TypeVar("K")
V = TypeVar("V")

_URL_RE = re.compile(r"""https?://[^\s<>"'`]+""", re.IGNORECASE)


class LimitedSizeDict(OrderedDict[K, V]):
    """带容量上限的 LRU 字典缓存。"""

    def __init__(self, maxsize: int = 256) -> None:
        super().__init__()
        self.maxsize = maxsize

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)


def safe_unlink(path: Path | None) -> None:
    """安全删除本地临时文件，文件不存在时不报错。"""
    if path is not None and path.is_file():
        path.unlink(missing_ok=True)


def fmt_size(file_path: Path) -> str:
    """格式化文件大小为易读字符串（KB/MB/GB）。"""
    if not file_path.is_file():
        return "0 B"
    size = float(file_path.stat().st_size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} GB"


def format_num(value: int | None) -> str:
    """将互动计数转换为万/亿等缩写展示。"""
    if value is None or value <= 0:
        return "0"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return str(value)


def generate_file_name(url: str, default_suffix: str = "") -> str:
    """根据 URL 计算唯一的 SHA256 文件名，保留原后缀。"""
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix
    if not suffix and default_suffix:
        suffix = default_suffix if default_suffix.startswith(".") else f".{default_suffix}"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"{digest}{suffix}"


def extract_json_url(data: dict[str, object] | str) -> str | None:
    """递归提取卡片 JSON 中首个有效 HTTP 链接。"""
    if isinstance(data, str):
        match = _URL_RE.search(data)
        if match is not None:
            return match.group(0)
        try:
            parsed_json = json.loads(data)
            if isinstance(parsed_json, dict):
                return extract_json_url(parsed_json)
        except (ValueError, TypeError):
            return None
        return None

    for value in data.values():
        if isinstance(value, str):
            match = _URL_RE.search(value)
            if match is not None:
                return match.group(0)
        elif isinstance(value, dict):
            found = extract_json_url(value)
            if found is not None:
                return found
    return None
