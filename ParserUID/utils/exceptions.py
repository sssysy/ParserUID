"""ParserUID 核心异常体系。"""

from __future__ import annotations


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
