"""NGA 网络请求与 GuestJs 自动探测。"""

from __future__ import annotations

import random
import asyncio
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlencode

import httpx

from .content import is_blocked_page, extract_guest_js
from ...utils.exceptions import ParseException


@dataclass(slots=True)
class NgaResponse:
    url: str
    status: int
    text: str
    set_cookies: list[str]


class NgaRequest:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        cookie: str = "",
        headers: dict[str, str],
        timeout: int = 30,
        retries: int = 2,
    ) -> None:
        self.client = client
        self.cookie = cookie
        self.headers = headers
        self.timeout = timeout
        self.retries = max(0, retries)
        self._dynamic_cookies: dict[str, str] = {}

    @staticmethod
    def canonical_url(tid: str, pid: str | None = None) -> str:
        query = {"tid": tid}
        if pid:
            query["pid"] = pid
        return f"https://bbs.nga.cn/read.php?{urlencode(query)}"

    @staticmethod
    def parse_identifiers(url: str) -> tuple[str, str | None]:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        tid = (query.get("tid") or [""])[0]
        pid = (query.get("pid") or [None])[0]
        if not tid.isdigit():
            fragment_pid = parse_qs(parsed.fragment).get("pid", [None])[0]
            if fragment_pid:
                pid = fragment_pid
        if not tid.isdigit():
            raise ParseException("NGA 链接缺少有效的 tid")
        return tid, pid if pid and pid.isdigit() else None

    def _headers_for(self) -> dict[str, str]:
        headers = self.headers.copy()
        cookie_parts = []
        if self.cookie:
            cookie_parts.append(self.cookie)
        for k, v in self._dynamic_cookies.items():
            cookie_parts.append(f"{k}={v}")
        if cookie_parts:
            headers["Cookie"] = "; ".join(cookie_parts)
        return headers

    async def _get_once(self, url: str) -> NgaResponse:
        try:
            response = await self.client.get(
                url,
                headers=self._headers_for(),
            )
            text = response.text
            content_type = response.headers.get("X-NGA-CONTENT-TYPE", "")
            if content_type.lower() == "short-message":
                text = "访客不能直接访问"
            set_cookies = list(response.headers.get_list("Set-Cookie"))
            return NgaResponse(
                url=str(response.url),
                status=response.status_code,
                text=text,
                set_cookies=set_cookies,
            )
        except Exception as exc:
            raise ParseException("NGA 请求失败，请稍后重试") from exc

    async def get(self, url: str) -> NgaResponse:
        response: NgaResponse | None = None
        for attempt in range(self.retries + 1):
            response = await self._get_once(url)
            if response.status == 200 and not is_blocked_page(response.text):
                return response
            if response.status == 403:
                guest_js = extract_guest_js(response.text)
                if guest_js:
                    self._dynamic_cookies["guestJs"] = guest_js
                    self._dynamic_cookies["lastpath"] = "0"
                    await asyncio.sleep(0.3)
                    sep = "&" if "?" in url else "?"
                    retry_url = f"{url}{sep}rand={random.randint(0, 999)}"
                    response = await self._get_once(retry_url)
                    if response.status == 200 and not is_blocked_page(response.text):
                        return response
            if attempt < self.retries:
                await asyncio.sleep(0.6 * (attempt + 1))
        if response is None:
            raise ParseException("NGA 请求失败")
        if response.status == 403:
            raise ParseException("NGA 拒绝访客访问，可能需要登录 Cookie")
        if response.status >= 400:
            raise ParseException(f"NGA 页面请求失败：HTTP {response.status}")
        raise ParseException("NGA 页面内容为空或受限")
