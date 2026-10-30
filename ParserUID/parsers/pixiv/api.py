"""Pixiv 网页 API 客户端。"""

from __future__ import annotations

import re
import json
import asyncio

import httpx

from ...utils.exceptions import ParseException

PIXIV_BASE = "https://www.pixiv.net"
PIXIV_API_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Referer": f"{PIXIV_BASE}/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
PIXIV_MEDIA_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Referer": f"{PIXIV_BASE}/",
}


class PixivAPI:
    """Pixiv 官方网页 Ajax API 客户端。"""

    def __init__(
        self,
        client: httpx.AsyncClient,
        cookie: str = "",
        retries: int = 2,
    ) -> None:
        self.client = client
        self.cookie = cookie
        self._retries = max(int(retries), 0)

    @property
    def headers(self) -> dict[str, str]:
        headers = PIXIV_API_HEADERS.copy()
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    async def _get(
        self,
        path: str,
        *,
        allow_not_found: bool = False,
    ) -> object:
        url = f"{PIXIV_BASE}{path}"
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = await self.client.get(
                    url,
                    headers=self.headers,
                )
                if response.status_code == 404 and allow_not_found:
                    return None
                if response.status_code >= 400:
                    if response.status_code >= 500 and attempt < self._retries:
                        raise ParseException(f"HTTP {response.status_code}")
                    raise ParseException(f"Pixiv API 返回 HTTP {response.status_code}")
                raw = response.text
            except (httpx.HTTPError, TimeoutError, UnicodeError) as exc:
                last_error = exc
                if attempt < self._retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise ParseException("Pixiv API 请求失败") from exc

            try:
                data = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ParseException("Pixiv API 返回数据格式异常") from exc
            if not isinstance(data, dict):
                raise ParseException("Pixiv API 返回数据格式异常")
            if data.get("error"):
                message = str(data.get("message") or "未知错误")[:200]
                raise ParseException(f"Pixiv API 错误: {message}")
            if "body" not in data:
                raise ParseException("Pixiv API 返回结果缺少 body")
            return data["body"]
        raise ParseException("Pixiv API 请求失败") from last_error

    async def get_illust_detail(self, pid: str) -> dict[str, object]:
        body = await self._get(f"/ajax/illust/{pid}")
        if not isinstance(body, dict):
            raise ParseException("Pixiv 作品数据格式异常")
        return {str(k): v for k, v in body.items()}

    async def get_illust_detail_optional(self, pid: str) -> dict[str, object] | None:
        try:
            body = await self._get(f"/ajax/illust/{pid}", allow_not_found=True)
        except ParseException:
            return None
        if body is not None and not isinstance(body, dict):
            raise ParseException("Pixiv 作品数据格式异常")
        return {str(k): v for k, v in body.items()} if isinstance(body, dict) else None

    async def get_pages(self, pid: str) -> list[dict[str, object]]:
        body = await self._get(f"/ajax/illust/{pid}/pages")
        if not isinstance(body, list):
            raise ParseException("Pixiv 作品页面数据格式异常")
        return [{str(k): v for k, v in item.items()} for item in body if isinstance(item, dict)]

    async def get_ugoira_meta(self, pid: str) -> dict[str, object] | None:
        try:
            body = await self._get(
                f"/ajax/illust/{pid}/ugoira_meta",
                allow_not_found=True,
            )
        except ParseException:
            return None
        if body is not None and not isinstance(body, dict):
            return None
        return {str(k): v for k, v in body.items()} if isinstance(body, dict) else None

    async def get_user_detail(self, uid: str) -> dict[str, object]:
        body = await self._get(f"/ajax/user/{uid}")
        if not isinstance(body, dict):
            raise ParseException("Pixiv 作者数据格式异常")
        return {str(k): v for k, v in body.items()}

    async def get_novel_detail(self, nid: str) -> dict[str, object]:
        body = await self._get(f"/ajax/novel/{nid}")
        if not isinstance(body, dict):
            raise ParseException("Pixiv 小说数据格式异常")
        return {str(k): v for k, v in body.items()}

    async def get_novel_detail_optional(self, nid: str) -> dict[str, object] | None:
        try:
            body = await self._get(f"/ajax/novel/{nid}", allow_not_found=True)
        except ParseException:
            return None
        if body is not None and not isinstance(body, dict):
            raise ParseException("Pixiv 小说数据格式异常")
        return {str(k): v for k, v in body.items()} if isinstance(body, dict) else None

    async def get_novel_series_total(self, series_id: str) -> int | None:
        try:
            body = await self._get(f"/ajax/novel/series/{series_id}")
        except ParseException:
            return None
        if not isinstance(body, dict):
            return None
        try:
            return int(str(body.get("total") or 0))
        except (KeyError, TypeError, ValueError):
            return None

    async def get_manga_series_total(
        self,
        series_id: str,
        user_id: str,
    ) -> int | None:
        url = f"{PIXIV_BASE}/user/{user_id}/series/{series_id}"
        try:
            response = await self.client.get(
                url,
                headers=self.headers,
            )
            if response.status_code != 200:
                return None
            html_text = response.text
        except Exception:
            return None
        matched = re.search(r"(\d+)部作品", html_text)
        if not matched:
            return None
        return int(matched.group(1))
