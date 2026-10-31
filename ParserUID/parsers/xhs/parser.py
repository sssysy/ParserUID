"""小红书平台笔记与视频链接解析器。"""

from __future__ import annotations

import re
import json
import asyncio
from typing import ClassVar
from urllib.parse import unquote, parse_qs, urlparse
from collections.abc import Sequence

import httpx
import msgspec
from curl_cffi.requests.exceptions import RequestException

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .utils import (
    build_note_url,
    is_xhs_page_url,
    note_ref_from_url,
)
from .models import (
    XhsNote,
    XhsUserPage,
    XhsUserPageData,
    select_video,
    select_live_photo_stream,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.helpers import build_image_blocks
from ...utils.exceptions import ParseException

INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>",
    flags=re.DOTALL,
)
UNDEFINED_VALUE_RE = re.compile(
    r"(?P<prefix>[:,\[]\s*)undefined(?=\s*[,}\]])"
    r"|(?P<scalar>undefined)"
)
JS_WRAPPER_RE = re.compile(r"\bnew\s+(?:Map|Set|WeakMap|WeakSet)\s*\(\s*(\[[\s\S]*?\])\s*\)")
NOTE_UNAVAILABLE_MARKERS = (
    "当前笔记暂时无法浏览",
    "当前笔记暂时无法查看",
    "笔记不存在",
    "笔记已删除",
)
LOGIN_PATH_MARKERS = ("/login", "/website-login")


def _unwrap_note(value: object, note_id: str) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    val_dict: dict[str, object] = {str(k): v for k, v in value.items()}
    if "note" in val_dict and isinstance(val_dict["note"], dict):
        val_dict = {str(k): v for k, v in val_dict["note"].items()}
    if "noteData" in val_dict and isinstance(val_dict["noteData"], dict):
        val_dict = {str(k): v for k, v in val_dict["noteData"].items()}
    if val_dict.get("noteId") == note_id or val_dict.get("title") is not None:
        return val_dict
    return None


def find_note_data(state: dict[str, object], note_id: str) -> dict[str, object] | None:
    """从探索页或详情页状态中提取笔记字典。"""
    note_data = state.get("noteData")
    if isinstance(note_data, dict):
        data = note_data.get("data")
        if isinstance(data, dict):
            if found := _unwrap_note(data.get("noteData"), note_id):
                return found
        if found := _unwrap_note(note_data.get("noteData"), note_id):
            return found
        if found := _unwrap_note(note_data, note_id):
            return found

    note = state.get("note")
    if isinstance(note, dict):
        detail_map = note.get("noteDetailMap")
        if isinstance(detail_map, dict):
            if found := _unwrap_note(detail_map.get(note_id), note_id):
                return found
            for value in detail_map.values():
                if found := _unwrap_note(value, note_id):
                    return found
    return None


def extract_initial_state(html: str) -> dict[str, object]:
    matched = INITIAL_STATE_RE.search(html)
    if not matched:
        if any(marker in html for marker in NOTE_UNAVAILABLE_MARKERS):
            raise ParseException("小红书笔记暂时无法浏览，可能需要登录 Cookie")
        raise ParseException("小红书链接已失效或笔记已删除")
    raw = matched.group(1).strip().rstrip(";")

    def replace_undefined(match: re.Match[str]) -> str:
        if match.group("prefix") is not None:
            return f"{match.group('prefix')}null"
        return "null"

    raw = JS_WRAPPER_RE.sub(r"\1", raw)
    raw = UNDEFINED_VALUE_RE.sub(replace_undefined, raw)
    raw = re.sub(r"\bNaN\b", "null", raw)
    raw = re.sub(r"""(?<![\w"\'])-?Infinity\b""", "null", raw)
    try:
        state = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ParseException("小红书页面数据格式异常") from exc
    if not isinstance(state, dict):
        raise ParseException("小红书页面数据格式异常")
    return {str(k): v for k, v in state.items()}


def is_unavailable_redirect(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.startswith("/404"):
        return True
    query = parse_qs(parsed.query)
    error_code = (query.get("error_code") or [""])[0]
    message = unquote((query.get("error_msg") or [""])[0])
    return error_code == "300031" or any(marker in message for marker in NOTE_UNAVAILABLE_MARKERS)


def is_login_redirect(url: str) -> bool:
    path = urlparse(url).path.lower().rstrip("/")
    return any(path == marker or path.startswith(f"{marker}/") for marker in LOGIN_PATH_MARKERS)


def decode_note_from_state(state: dict[str, object], note_id: str) -> XhsNote:
    raw = find_note_data(state, note_id)
    if raw is None:
        raise ParseException("小红书笔记不存在、已删除或当前不可见")
    try:
        note = msgspec.convert(raw, type=XhsNote)
    except (msgspec.ValidationError, TypeError, ValueError) as exc:
        raise ParseException("小红书笔记数据格式异常") from exc
    if not note.note_id:
        note.note_id = note_id
    return note


class XhsParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="xhs", display_name="小红书")

    @property
    def web_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Safari/537.36"
            ),
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.xiaohongshu.com/",
            "Origin": "https://www.xiaohongshu.com",
        }
        cookie = self.get_config_value("cookie").strip() or self.get_config_value("cookies").strip()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @property
    def media_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.xiaohongshu.com/",
        }
        cookie = self.get_config_value("cookie").strip() or self.get_config_value("cookies").strip()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @staticmethod
    def _matched_url(searched: re.Match[str]) -> str:
        value = searched.group(0)
        raw = searched.string.strip()
        if raw.startswith(("http://", "https://")) and value in raw and " " not in raw:
            value = raw
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value

    @handle("xhslink.com", r"xhslink\.com/[A-Za-z0-9._?%&+=/#@-]+")
    @handle("xhslink.cn", r"xhslink\.cn/[A-Za-z0-9._?%&+=/#@-]+")
    async def _parse_short_link(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        url = self._matched_url(searched)
        final_url = await self._resolve_short_link(url)
        if final_url == url:
            raise ParseException("无法展开小红书短链")
        note_id, token = note_ref_from_url(final_url)
        if note_id:
            return await self._parse_note(note_id, token, final_url)
        return await self.parse_with_redirect(final_url, headers=self.web_headers)

    async def _resolve_short_link(self, url: str) -> str:
        """多次追踪重定向解析小红书短链。"""
        current = url
        client = await self.get_curl_client()
        for _ in range(4):
            try:
                response = await client.get(
                    current,
                    headers=self.web_headers,
                    allow_redirects=True,
                    timeout=30,
                )
                final_url = str(response.url)
            except (httpx.HTTPError, RequestException, TimeoutError, UnicodeError):
                final_url = current
            if note_ref_from_url(final_url)[0]:
                return final_url
            next_url = await self._extract_redirect_from_page(final_url)
            if not next_url or next_url == current or not is_xhs_page_url(next_url):
                return final_url
            current = next_url
        return current

    async def _extract_redirect_from_page(self, url: str) -> str | None:
        try:
            client = await self.get_curl_client()
            response = await client.get(
                url,
                headers=self.web_headers,
                allow_redirects=True,
                timeout=30,
            )
            if response.status_code >= 400:
                return None
            html = response.text
        except (httpx.HTTPError, RequestException, TimeoutError, UnicodeError):
            return None

        candidates = (
            re.search(
                r"""(?:window\.)?location(?:\.href)?\s*=\s*["']([^"']+)["']""",
                html,
            ),
            re.search(r"""<meta[^>]+url=([^"'>\s]+)""", html, re.IGNORECASE),
            re.search(
                r"""["'](?:redirectPath|jumpUrl|targetUrl)["']\s*:\s*["']([^"']+)["']""",
                html,
            ),
        )
        for candidate in candidates:
            if not candidate:
                continue
            target = unquote(candidate.group(1).replace(r"\/", "/"))
            if target.startswith(("http://", "https://")) and is_xhs_page_url(target):
                return target
        return None

    @handle(
        "xiaohongshu.com",
        r"(?:www\.)?xiaohongshu\.com/"
        r"(?:explore|discovery/item|search_result)/(?P<note_id>[0-9A-Za-z]+)"
        r"(?:[/?#][^\s]*)?",
    )
    async def _parse_common(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        url = self._matched_url(searched)
        note_id, token = note_ref_from_url(url)
        if not note_id:
            raise ParseException("无法从链接中提取小红书笔记 ID")
        return await self._parse_note(note_id, token, url)

    @handle(
        "target_note_id",
        r"xiaohongshu\.com/[^\s]*[?&]target_note_id="
        r"(?P<note_id>[0-9A-Za-z]+)(?:[^\s]*)?",
    )
    async def _parse_target_note(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        url = self._matched_url(searched)
        note_id, token = note_ref_from_url(url)
        if not note_id:
            note_id = searched.group("note_id")
        return await self._parse_note(note_id, token, url)

    async def _parse_note(
        self,
        note_id: str,
        token: str | None,
        source_url: str,
    ) -> Sequence[ContentItem]:
        last_error: ParseException | None = None
        for candidate in self._candidate_urls(note_id, token, source_url):
            try:
                note = await self._fetch_note(candidate, note_id)
                return await self._build_result(note, note_id, token)
            except ParseException as exc:
                last_error = exc
                logger.debug(f"[小红书] 候选链接失败 | note_id={note_id} | error={exc}")
        raise last_error or ParseException("小红书笔记解析失败")

    @staticmethod
    def _candidate_urls(
        note_id: str,
        token: str | None,
        source_url: str,
    ) -> list[str]:
        urls: list[str] = []
        if is_xhs_page_url(source_url) and note_id in source_url:
            urls.append(source_url)
        for path in ("discovery/item", "explore"):
            urls.append(build_note_url(note_id, token, path=path))
        return list(dict.fromkeys(urls))

    async def _fetch_note(self, url: str, note_id: str) -> XhsNote:
        client = await self.get_curl_client()
        retries = 2
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    url,
                    headers=self.web_headers,
                    allow_redirects=True,
                    timeout=30,
                )
                if response.status_code >= 400:
                    raise ParseException(f"HTTP 请求错误 {response.status_code}")
                html = response.text
                response_url = str(response.url)
            except (httpx.HTTPError, RequestException, TimeoutError, UnicodeError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise ParseException("小红书页面请求失败") from exc

            if is_login_redirect(response_url):
                raise ParseException("小红书登录状态已失效，请更新 Cookie")
            if is_unavailable_redirect(response_url):
                raise ParseException("小红书笔记暂时无法浏览")
            state = extract_initial_state(html)
            return decode_note_from_state(state, note_id)
        raise ParseException("小红书页面请求失败") from last_error

    async def _build_result(
        self,
        note: XhsNote,
        note_id: str,
        token: str | None,
    ) -> Sequence[ContentItem]:
        profile = await self._fetch_author_profile(note.author_id)
        desc_text = profile.basic_info.desc or profile.basic_info.ip_location or None if profile else None
        author = self.create_author(
            (profile.basic_info.nickname if profile else "") or note.author_name,
            note.avatar_url,
            description=desc_text,
            uid=note.author_id,
            metrics=profile.metrics if profile else (),
        )
        display_url = build_note_url(note_id, token)
        title = note.title.strip() or "小红书笔记"
        cover = note.cover_url or None

        if note.video is not None:
            quality_pref = self.get_config_value("video_quality", "auto")
            selected = select_video(
                note.video,
                quality_pref,
            )
            if selected is None:
                raise ParseException("未找到可用的视频流")
            await self.send_info(
                title=title,
                card_type="video",
                author=author,
                cover_url=cover,
                video_duration=f"{int(note.video.duration)}秒" if note.video.duration > 0 else None,
                video_quality=selected.quality_label,
                text=note.text,
                url=display_url,
                metrics=note.metrics,
            )
            content = self.create_video_content(
                selected.url,
                duration=note.video.duration,
                headers=self.media_headers,
                proxy=self.proxy,
                fallback_urls=selected.fallback_urls,
            )
            return self.result([content])

        image_urls = note.image_urls
        if not image_urls:
            raise ParseException("小红书笔记没有可发送的媒体")
        contents = self._build_image_contents(note)
        await self.send_info(
            title=title,
            card_type="common",
            author=author,
            cover_url=cover,
            text=note.text,
            url=display_url,
            metrics=note.metrics,
            blocks=build_image_blocks(image_urls, limit=self.card_image_limit),
        )
        if note.text:
            contents.insert(0, self.create_text_content(note.text))
        return self.result(contents)

    async def _fetch_author_profile(self, user_id: str | None) -> XhsUserPageData | None:
        cookie = self.get_config_value("cookie").strip() or self.get_config_value("cookies").strip()
        if not user_id or not cookie:
            return None
        url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
        try:
            client = await self.get_curl_client()
            response = await client.get(
                url,
                headers=self.web_headers,
                allow_redirects=True,
                timeout=30,
            )
            if response.status_code >= 400:
                return None
            html = response.text
        except (httpx.HTTPError, RequestException, TimeoutError, UnicodeError):
            return None
        try:
            state = extract_initial_state(html)
            user = state.get("user")
            if not isinstance(user, dict):
                return None
            page = msgspec.convert(user, type=XhsUserPage)
            return page.user_page_data
        except (ParseException, msgspec.ValidationError, TypeError, ValueError):
            return None

    def _build_image_contents(self, note: XhsNote) -> list[ContentItem]:
        contents: list[ContentItem] = []
        for image in note.image_list:
            image_url = image.image_url
            if not image_url:
                continue
            if image.live_photo:
                stream = select_live_photo_stream(image)
                if stream is not None:
                    contents.append(
                        self.create_live_photo_content(
                            stream.url,
                            image_url,
                            headers=self.media_headers,
                            proxy=self.proxy,
                            fallback_urls=stream.fallback_urls,
                        )
                    )
                    continue
            contents.extend(self.create_image_contents([image_url], headers=self.media_headers, proxy=self.proxy))
        return contents
