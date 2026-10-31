"""Iwara 请求层：curl_cffi 浏览器指纹、Cookie 与重试。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final
from collections.abc import Mapping

if TYPE_CHECKING:
    from curl_cffi.requests import AsyncSession

from ...utils.exceptions import ParseException

API_HOST: Final[str] = "api.iwara.tv"
IMPERSONATE: Final[str] = "chrome120"
CHALLENGE_MARKERS: Final[tuple[str, ...]] = (
    "just a moment",
    "cf-chl-",
    "challenge-platform",
)


def is_challenge_page(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


class IwaraClient:
    """Iwara 请求客户端，封装 TLS 指纹伪装与请求头。"""

    def __init__(
        self,
        *,
        cookie: str,
        headers: Mapping[str, str],
        proxy: str | None = None,
        timeout: int = 30,
        retries: int = 2,
    ) -> None:
        self.cookie = cookie
        self.headers = dict(headers)
        self.proxy = proxy
        self.timeout = timeout
        self.retries = max(0, retries)
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            try:
                from curl_cffi.requests import AsyncSession as _AsyncSession
            except ImportError as exc:
                raise ParseException("未安装 curl_cffi，无法调用 Iwara 接口") from exc
            self._session = _AsyncSession(
                headers=self.headers,
                timeout=self.timeout,
                impersonate=IMPERSONATE,
                proxy=self.proxy,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            session = self._session
            self._session = None
            await session.close()

    def _request_headers(self) -> dict[str, str]:
        headers = self.headers.copy()
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    async def fetch_api(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self.session.get(
                    url,
                    headers=self._request_headers(),
                )
                if response.status_code == 404:
                    raise ParseException("Iwara 作品不存在或已被删除")
                if response.status_code == 403 and is_challenge_page(response.text):
                    raise ParseException("Iwara 触发了 Cloudflare 验证，请稍后重试或更新 Cookie")
                if response.status_code >= 400:
                    raise ParseException(f"Iwara 请求失败（HTTP {response.status_code}）")
                return response.content
            except ParseException:
                raise
            except (ValueError, Exception) as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(1 + attempt)
        raise ParseException(f"获取 Iwara 作品信息失败：{last_error}") from last_error

    async def fetch_video_files(
        self,
        file_url: str,
        *,
        x_version: str,
    ) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                headers = self._request_headers()
                headers["x-version"] = x_version
                response = await self.session.get(
                    file_url,
                    headers=headers,
                )
                if response.status_code >= 400:
                    raise ParseException(f"Iwara 视频画质请求失败（HTTP {response.status_code}）")
                return response.content
            except ParseException:
                raise
            except (ValueError, Exception) as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(1 + attempt)
        raise ParseException(f"获取 Iwara 视频画质失败：{last_error}") from last_error
