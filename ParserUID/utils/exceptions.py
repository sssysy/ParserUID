"""ParserUID 核心异常体系。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

DEFAULT_USER_ERROR = "解析失败，请稍后再试"
USER_ERROR_MAX_LENGTH = 300

_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_AUTH_SCHEME_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_SENSITIVE_KV_RE = re.compile(
    r"\b("
    r"cookie|set-cookie|authorization|access[_-]?token|refresh[_-]?token|"
    r"token|api[_-]?key|secret|signature|sig|password|passwd|"
    r"session(?:id)?|ticket|credential"
    r")\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Z]:\\(?:[^\\\s]+\\)*[^\\\s]+", re.IGNORECASE)
_POSIX_PATH_RE = re.compile(r"(?<![\w:/])/(?:[^\s/:]+/)+[^\s/:，。；：！？、]+")


class ParseException(Exception):
    """解析业务基础异常，向用户展示简短错误提示。"""

    def __init__(self, message: str = "解析失败") -> None:
        super().__init__(message)
        self.message = message


class ParserConfigException(ParseException):
    """解析器配置读取或格式校验异常。"""


class ParserRegistryException(ParseException):
    """解析器发现、校验或关键词冲突异常。"""


class RedirectException(ParseException):
    """短链重定向追踪失败异常。"""


class ResourceLimitException(ParseException):
    """媒体大小或时长超出安全限制异常。"""


class NetworkException(ParseException):
    """外部 API 或媒体资源网络请求异常。"""


class RegionRestrictedException(ParseException):
    """内容受地区或版权限制，向用户展示明确原因。"""


class DownloadException(ParseException):
    """通用多媒体下载异常。"""


def sanitize_error_text(text: str, *, hide_local_paths: bool = False) -> str:
    """清理异常文本，移除 URL 查询参数和常见凭据。"""
    normalized = " ".join(_CONTROL_RE.sub(" ", text).split())
    normalized = _URL_RE.sub(_redact_url, normalized)
    normalized = _SENSITIVE_KV_RE.sub(_redact_sensitive_value, normalized)
    normalized = _AUTH_SCHEME_RE.sub(r"\1 <redacted>", normalized)
    if hide_local_paths:
        normalized = _WINDOWS_PATH_RE.sub("<path>", normalized)
        normalized = _POSIX_PATH_RE.sub("<path>", normalized)
    return normalized


def format_user_error(exc: Exception) -> str:
    """把业务异常转换为可安全发送给用户的简短提示。"""
    if not isinstance(exc, ParseException):
        return DEFAULT_USER_ERROR

    detail = sanitize_error_text(exc.message, hide_local_paths=True)
    if not detail or detail == "解析失败":
        return DEFAULT_USER_ERROR
    return _truncate(detail, USER_ERROR_MAX_LENGTH)


def _redact_url(matched: re.Match[str]) -> str:
    raw = matched.group(0)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return _REDACTED_URL

    host = parsed.hostname
    if parsed.scheme.lower() not in {"http", "https"} or host is None:
        return _REDACTED_URL

    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))


def _redact_sensitive_value(matched: re.Match[str]) -> str:
    return f"{matched.group(1)}=<redacted>"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


_REDACTED_URL = "https://<redacted>/"
