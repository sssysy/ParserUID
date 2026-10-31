"""知乎网络请求与反爬签名会话层。"""

from __future__ import annotations

import json
import asyncio
from typing import Final
from urllib.parse import urlparse
from collections.abc import Mapping, Callable

import httpx
from bs4 import BeautifulSoup

from gsuid_core.logger import logger

from .sign import sign_zhihu_fetch_request
from ...utils.exceptions import ParseException

_CHALLENGE_MARKERS: Final[tuple[str, ...]] = (
    'id="zh-zse-ck"',
    "static.zhihu.com/zse-ck/",
    'appname":"zse_ck"',
)
_LOGIN_TITLE: Final[str] = "知乎 - 有问题，就会有答案"


def extract_initial_data(html_text: str) -> dict[str, object] | None:
    soup = BeautifulSoup(html_text, "html.parser")
    node = soup.select_one('script#js-initialData[type="text/json"]')
    if node is None:
        return None
    raw = node.get_text(strip=True)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    state = payload.get("initialState")
    return {str(k): v for k, v in payload.items()} if isinstance(state, dict) else None


def initial_entities(initial_data: Mapping[str, object]) -> Mapping[str, object]:
    state = initial_data.get("initialState")
    if not isinstance(state, Mapping):
        return {}
    entities = state.get("entities")
    return entities if isinstance(entities, Mapping) else {}


def is_challenge_page(text: str, status_code: int) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS) or (status_code == 403 and "zse-ck" in lowered)


def is_login_page(final_url: str, text: str) -> bool:
    lowered_url = final_url.lower()
    lowered_text = text.lower()
    return "/signin" in lowered_url or "/signup" in lowered_url or _LOGIN_TITLE in text or _LOGIN_TITLE in lowered_text


def parse_cookie_string(cookie_str: str) -> dict[str, str]:
    """将 Cookie 请求头字符串解析为键值对字典。"""
    result: dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, val = part.split("=", 1)
        result[key.strip()] = val.strip()
    return result


class ZhihuClient:
    """知乎请求会话管理器，提供 TLS 伪装与自动反爬签名。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        cookie_str: str = "",
        headers: Mapping[str, str],
        retries: int = 2,
    ) -> None:
        self.client = client
        self.cookie_str = cookie_str
        self.cookies_dict = parse_cookie_string(cookie_str)
        self.headers = dict(headers)
        self.retries = max(0, retries)
        self._homepage_ready = False
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        self._homepage_ready = False

    @property
    def dc0(self) -> str:
        return self.cookies_dict.get("d_c0", "")

    def _request_headers(
        self,
        url: str,
        *,
        accept: str,
        sign: bool,
    ) -> dict[str, str]:
        token = self.cookies_dict.get("_xsrf", "")
        headers = {
            **self.headers,
            "accept": accept,
            "referer": "https://www.zhihu.com/",
            "origin": "https://www.zhihu.com",
        }
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str
        if token:
            headers["x-xsrftoken"] = token
        if sign:
            headers.update(sign_zhihu_fetch_request(url, dc0=self.dc0))
        return headers

    async def ensure_homepage(self) -> None:
        if self._homepage_ready:
            return
        async with self._lock:
            if self._homepage_ready:
                return
            await self._request_text(
                "https://www.zhihu.com/",
                accept=("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
                sign=False,
                allow_login=False,
            )
            self._homepage_ready = True

    async def fetch_api(
        self,
        url: str,
        *,
        method: str = "GET",
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        await self.ensure_homepage()
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.request(
                    method,
                    url,
                    headers=self._request_headers(
                        url,
                        accept="application/json, text/plain, */*",
                        sign=True,
                    ),
                    json=json_body,
                )
                final_url = str(response.url)
                text = response.text
                status = int(response.status_code)
                if response.url.host != "www.zhihu.com":
                    raise ParseException("知乎请求被重定向到非预期站点")
                if is_challenge_page(text, status):
                    raise ParseException("知乎请求触发盾验证，请补充 Cookie")
                if is_login_page(final_url, text):
                    raise ParseException("知乎登录状态失效，请更新 Cookie")
                if status in (401, 403):
                    raise ParseException("知乎接口无权限，请登录或补充 Cookie")
                if status >= 400:
                    raise ParseException(f"知乎接口请求失败：HTTP {status}")
                payload = self._json_payload(text)
                if payload is None:
                    raise ParseException("知乎接口返回格式异常")
                return payload
            except ParseException:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(1 + attempt)
        logger.warning(f"[知乎] 接口请求失败 | host={urlparse(url).hostname or 'unknown'} | error={last_error}")
        raise ParseException("知乎接口请求失败") from last_error

    async def fetch_page(
        self,
        url: str,
        *,
        validator: Callable[[Mapping[str, object]], bool] | None = None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        await self.ensure_homepage()
        last_error: Exception | None = None
        saw_challenge = False
        saw_login = False
        saw_invalid = False
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.get(
                    url,
                    headers=self._request_headers(
                        url,
                        accept=(
                            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
                        ),
                        sign=False,
                    ),
                )
                text = response.text
                status = int(response.status_code)
                final_url = str(response.url)
                if is_challenge_page(text, status):
                    saw_challenge = True
                    continue
                if is_login_page(final_url, text):
                    saw_login = True
                    continue
                initial = extract_initial_data(text)
                if initial is None:
                    saw_invalid = True
                    continue
                if validator is None or validator(initial):
                    return initial, self._request_headers(
                        url,
                        accept="text/html,*/*",
                        sign=False,
                    )
                saw_invalid = True
            except Exception as exc:
                last_error = exc
            if attempt < self.retries:
                await asyncio.sleep(1 + attempt)
        if saw_challenge:
            raise ParseException("知乎页面触发盾拦截，请补充 Cookie")
        if saw_login:
            raise ParseException("知乎登录态已失效，请更新 Cookie")
        if saw_invalid:
            raise ParseException("知乎页面结构已变化或不可访问")
        logger.warning(f"[知乎] 页面请求失败 | host={urlparse(url).hostname or 'unknown'} | error={last_error}")
        raise ParseException("知乎页面请求失败") from last_error

    async def _request_text(
        self,
        url: str,
        *,
        accept: str,
        sign: bool,
        allow_login: bool,
    ) -> str:
        response = await self.client.get(
            url,
            headers=self._request_headers(url, accept=accept, sign=sign),
        )
        text = response.text
        status = int(response.status_code)
        final_url = str(response.url)
        if is_challenge_page(text, status):
            raise ParseException("知乎访问触发盾验证，请补充 Cookie")
        if not allow_login and is_login_page(final_url, text):
            return text
        if status >= 400:
            raise ParseException(f"知乎主页请求失败：HTTP {status}")
        return text

    @staticmethod
    def _json_payload(text: str) -> dict[str, object] | None:
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return None
        return {str(k): v for k, v in payload.items()} if isinstance(payload, dict) else None
