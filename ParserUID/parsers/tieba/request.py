"""贴吧 Protobuf 网络请求客户端。"""

from __future__ import annotations

import asyncio
from typing import Final

import httpx
from google.protobuf.message import DecodeError

from gsuid_core.logger import logger

from .PbPageResIdl_pb2 import PbPageResIdl
from ...utils.exceptions import ParseException

PAGE_URL: Final[str] = "https://tiebac.baidu.com/c/f/pb/page"
BOUNDARY: Final[str] = "-*_r1999"
RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({408, 429, 500, 502, 503, 504})


def _varint(value: int) -> bytes:
    val = (value & ((1 << 64) - 1)) if value < 0 else value
    output = bytearray()
    while val > 0x7F:
        output.append((val & 0x7F) | 0x80)
        val >>= 7
    output.append(val)
    return bytes(output)


def _int_field(number: int, value: int) -> bytes:
    return _varint(number << 3) + _varint(value)


def _message_field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _string_field(number: int, value: str) -> bytes:
    return _message_field(number, value.encode())


def build_page_request(tid: int) -> bytes:
    """构造贴吧移动端 protobuf 请求报文。"""
    common = _int_field(1, 2) + _string_field(2, "12.64.1.1")
    data = (
        _message_field(25, common)
        + _int_field(4, tid)
        + _int_field(18, 1)
        + _int_field(13, 30)
        + _int_field(6, 0)
        + _int_field(5, 0)
        + _int_field(8, 1)
        + _int_field(9, 5)
        + _int_field(74, 1)
    )
    return _message_field(1, data)


def multipart_body(payload: bytes) -> bytes:
    return (
        (f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="data"; filename="file"\r\n\r\n').encode()
        + payload
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )


class TiebaClient:
    """贴吧 Protobuf API 请求管理器。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        retries: int = 2,
    ) -> None:
        self.client = client
        self.retries = max(0, retries)

    async def fetch_page(self, tid: int) -> PbPageResIdl:
        headers = {
            "x_bd_data_type": "protobuf",
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip",
            "User-Agent": "miku/39",
            "Host": "tiebac.baidu.com",
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
        }
        body = multipart_body(build_page_request(tid))
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.post(
                    PAGE_URL,
                    params={"cmd": 302001},
                    headers=headers,
                    content=body,
                )
                raw = response.content
                if response.status_code in RETRYABLE_STATUSES:
                    if attempt < self.retries:
                        await asyncio.sleep(1 + attempt)
                        continue
                    raise ParseException("贴吧接口暂时不可用，请稍后重试")
                if response.status_code >= 400:
                    raise ParseException(f"贴吧接口请求失败：HTTP {response.status_code}")
            except ParseException:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise ParseException("贴吧接口请求失败，请稍后重试") from exc

            result = PbPageResIdl()
            try:
                result.ParseFromString(raw)
            except DecodeError as exc:
                raise ParseException("贴吧接口返回数据解析异常") from exc
            if result.error.errorno:
                logger.warning(f"[百度贴吧] 接口返回错误 | tid={tid} | code={result.error.errorno}")
                raise ParseException("贴吧帖子不存在或已被删除")
            return result
        raise ParseException("贴吧接口请求失败") from last_error
