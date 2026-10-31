"""微博平台动态、视频与头条文章链接解析器。"""

from __future__ import annotations

import re
import json
import time
import asyncio
from typing import ClassVar
from urllib.parse import unquote, parse_qs, urlparse
from collections.abc import Sequence

import httpx
import msgspec

from gsuid_core.logger import logger

from ..base import BaseParser, handle
from .models import (
    WeiboStatus,
    decode_status,
    decode_comments,
    decode_long_text,
    parse_weibo_timestamp,
    format_weibo_timestamp,
)
from ..config import ParserItemConfig
from .content import comment_text, clean_status_text, parse_article_blocks
from ...sender import ContentItem
from ...utils.models import Platform
from ...render.models import (
    Author,
    Metric,
    Comment,
    CardBlock,
    PreviewCard,
    CommentReply,
)
from ...utils.helpers import build_card_blocks
from ...utils.exceptions import ParseException

VISITOR_GEN_URL = "https://passport.weibo.com/visitor/genvisitor"
VISITOR_URL = "https://passport.weibo.com/visitor/visitor"
VISITOR_HOSTS = frozenset({"passport.weibo.com", "visitor.passport.weibo.cn"})


def _count(value: object) -> str | None:
    try:
        number = int(str(value or 0))
    except (TypeError, ValueError):
        return None
    if abs(number) >= 100_000_000:
        text = f"{number / 100_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}亿"
    if abs(number) >= 10_000:
        text = f"{number / 10_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return str(number)


def _metric(label: str, value: object) -> Metric | None:
    formatted = _count(value)
    return Metric(label=label, value=formatted) if formatted else None


def _metrics(*items: tuple[str, object]) -> tuple[Metric, ...]:
    results: list[Metric] = []
    for label, value in items:
        m = _metric(label, value)
        if m is not None:
            results.append(m)
    return tuple(results)


def _article_id(source_url: str) -> str:
    if match := re.search(r"/id/(\d+)", source_url):
        return match.group(1)
    if match := re.search(r"#*/id=(\d+)", source_url):
        return match.group(1)
    if match := re.search(r"(?:[?#]|&)id=(\d+)", source_url):
        return match.group(1)
    query = parse_qs(urlparse(source_url).query)
    for key in ("id", "article_id"):
        value = query.get(key)
        if value:
            return value[0]
    return ""


def parsed_fid(matched: str) -> str:
    value = parse_qs(matched).get("fid")
    return value[0] if value else ""


def comment_limit(value: object) -> int:
    try:
        return max(0, min(int(str(value or 5)), 20))
    except (TypeError, ValueError):
        return 5


def _is_visitor_response(response: httpx.Response) -> bool:
    return urlparse(str(response.url)).hostname in VISITOR_HOSTS


def _decode_jsonp(text: str, callback: str) -> object:
    marker = f"{callback}("
    start = text.find(marker)
    if start < 0:
        raise ParseException("微博游客会话获取失败")
    start += len(marker)
    end = text.rfind(")")
    if end <= start:
        raise ParseException("微博游客会话获取失败")
    payload: object = msgspec.json.decode(text[start:end])
    return payload


def _visitor_ticket(payload: object) -> tuple[str, bool, int]:
    if not isinstance(payload, dict):
        raise ParseException("微博游客会话获取失败")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ParseException("微博游客会话获取失败")
    tid = data.get("tid")
    if not isinstance(tid, str) or not tid:
        raise ParseException("微博游客会话获取失败")
    new_tid = data.get("new_tid") is True
    raw_confidence = data.get("confidence", 100)
    confidence = raw_confidence if type(raw_confidence) is int else 100
    return tid, new_tid, confidence


async def _response_json(response: httpx.Response) -> dict[str, object]:
    payload: object = msgspec.json.decode(response.content)
    if not isinstance(payload, dict):
        raise ParseException("微博接口响应格式异常")
    return {str(key): value for key, value in payload.items()}


class WeiboParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="weibo", display_name="微博")

    def __init__(self, config: ParserItemConfig | None = None) -> None:
        super().__init__(config)
        self._visitor_session_ready = False

    @property
    def media_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Safari/537.36"
            ),
            "Referer": "https://weibo.com/",
        }
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @handle(
        "weibo.com/tv",
        r"weibo\.com/tv/show/\d{4}:\d+(?:\?mid=(?P<mid>\d+))?",
    )
    async def _parse_tv(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        mid = searched.groupdict().get("mid")
        if mid:
            return await self.parse_status(self._mid2id(mid))
        source = self._matched_url(searched)
        fid = parse_qs(urlparse(source).query).get("fid", [""])[0]
        if not fid:
            match = re.search(r"/show/(\d{4}:\d+)", source)
            fid = match.group(1) if match else ""
        if not fid:
            raise ParseException("无法从微博视频链接中提取 fid")
        return await self.parse_video(fid)

    @handle(
        "video.weibo.com/show",
        r"video\.weibo\.com/show\?(?P<query>[^#\s]*\bfid=\d+:\d+[^#\s]*)",
    )
    async def _parse_video(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        query_val = searched.groupdict().get("query") or ""
        fid = parsed_fid(query_val)
        return await self.parse_video(fid)

    @handle(
        "m.weibo.cn",
        r"(?:m\.)?weibo\.cn/(?:status|detail|\d+)/(?P<wid>[0-9A-Za-z]+)",
    )
    @handle(
        "weibo.com",
        r"(?:www\.)?weibo\.com/\d+/(?P<wid>[0-9A-Za-z]+)",
    )
    async def _parse_status(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        wid = searched.groupdict().get("wid") or ""
        return await self.parse_status(wid)

    @handle(
        "mapp.api.weibo",
        r"mapp\.api\.weibo\.cn/fx/[0-9A-Za-z]+\.html",
    )
    async def _parse_short_link(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        source = self._matched_url(searched)
        final_url = await self.get_final_url(source, headers=self.media_headers)
        candidates = [final_url]
        query = parse_qs(urlparse(final_url).query)
        candidates.extend(unquote(value) for value in query.get("url", []))
        for candidate in candidates:
            for pattern in (
                r"weibo\.cn/(?:status|detail|\d+)/(?P<wid>[0-9A-Za-z]+)",
                r"weibo\.com/(?P<uid>\d+)/(?P<wid>[0-9A-Za-z]+)",
            ):
                if match := re.search(pattern, candidate):
                    return await self.parse_status(match.group("wid"))
        raise ParseException("微博短链接未指向可解析的微博")

    @handle(
        "weibo.com/ttarticle",
        r"weibo\.com/ttarticle/(?:p/show|x/m/show)[^#\s]*[?#][^\s]*id=(?P<id>\d+)",
    )
    @handle(
        "card.weibo.com/article",
        r"card\.weibo\.com/article/m/show/id/(?P<id>\d+)",
    )
    async def _parse_article(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        article_id = searched.groupdict().get("id")
        if not article_id:
            article_id = _article_id(self._matched_url(searched))
        if not article_id:
            raise ParseException("无法从微博头条链接中提取文章 ID")
        return await self.parse_article(article_id)

    def _matched_url(self, searched: re.Match[str]) -> str:
        source = str(getattr(searched, "string", "") or "")
        start = source.rfind("http://")
        if start < 0:
            start = source.rfind("https://")
        if start >= 0:
            return source[start:]
        matched = searched.group(0)
        return matched if matched.startswith(("http://", "https://")) else ""

    async def _refresh_visitor_session(self) -> None:
        client = await self.get_client()
        chrome_version = re.search(r"Chrome/(\d+)", self.media_headers["User-Agent"])
        browser = f"Chrome{chrome_version.group(1) if chrome_version is not None else '132'},0,0,0"
        fingerprint = json.dumps(
            {
                "os": "1",
                "browser": browser,
                "fonts": "undefined",
                "screenInfo": "1920*1080*24",
                "plugins": "",
            },
            separators=(",", ":"),
        )
        headers = {
            "User-Agent": self.media_headers["User-Agent"],
            "Referer": "https://passport.weibo.com/visitor/visitor",
        }
        generated = await client.post(
            VISITOR_GEN_URL,
            data={"cb": "gen_callback", "fp": fingerprint},
            headers=headers,
        )
        if generated.status_code >= 400:
            raise ParseException(f"微博游客会话获取失败：HTTP {generated.status_code}")
        ticket = _visitor_ticket(_decode_jsonp(generated.text, "gen_callback"))
        tid, new_tid, confidence = ticket
        incarnated = await client.get(
            VISITOR_URL,
            params={
                "a": "incarnate",
                "t": tid,
                "w": 3 if new_tid else 2,
                "c": f"{confidence:03d}",
                "gc": "",
                "cb": "cross_domain",
                "from": "weibo",
                "_rand": time.time(),
            },
            headers=headers,
        )
        if incarnated.status_code >= 400:
            raise ParseException(f"微博游客会话获取失败：HTTP {incarnated.status_code}")
        self._visitor_session_ready = True

    async def _ensure_visitor_session(self) -> None:
        if self._visitor_session_ready:
            return
        if self.get_config_value("cookie").strip():
            self._visitor_session_ready = True
            return
        await self._refresh_visitor_session()

    async def _request_json(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        error: str,
    ) -> dict[str, object]:
        await self._ensure_visitor_session()
        client = await self.get_client()
        request_headers = {**self.media_headers, **(headers or {})}
        retries = 2
        last_error: Exception | None = None
        attempt = 0
        visitor_refreshed = False
        while attempt <= retries:
            try:
                if data is not None:
                    response = await client.post(url, data=data, headers=request_headers)
                else:
                    response = await client.get(url, params=params, headers=request_headers)
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    attempt += 1
                    await asyncio.sleep(1 + attempt)
                    continue
                raise ParseException(error) from exc
            if _is_visitor_response(response):
                if visitor_refreshed:
                    raise ParseException(f"{error}：微博游客会话无效")
                await self._refresh_visitor_session()
                visitor_refreshed = True
                continue
            if response.status_code >= 400:
                raise ParseException(f"HTTP 请求错误 {response.status_code}")
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower():
                raise ParseException(f"{error}：接口返回非 JSON")
            return await _response_json(response)
        raise ParseException(error) from last_error

    async def _fetch_status(self, weibo_id: str) -> WeiboStatus:
        payload = await self._request_json(
            "https://m.weibo.cn/statuses/show",
            params={"id": weibo_id, "_": int(time.time() * 1000)},
            headers={
                "Referer": f"https://m.weibo.cn/detail/{weibo_id}",
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "mweibo-pwa": "1",
            },
            error="获取微博详情失败",
        )
        try:
            return decode_status(payload)
        except (TypeError, ValueError) as exc:
            raise ParseException("微博内容不存在或已被删除") from exc

    async def _long_text(self, status: WeiboStatus) -> str | None:
        if not status.is_long_text:
            return None
        try:
            payload = await self._request_json(
                f"https://m.weibo.cn/statuses/extend?id={status.mid}",
                headers={
                    "Referer": f"https://m.weibo.cn/detail/{status.mid}",
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                },
                error="获取微博长文失败",
            )
            content = decode_long_text(payload)
            return clean_status_text(content, "") or None
        except ParseException as exc:
            logger.warning(f"[微博] 长文获取失败，降级使用摘要 | mid={status.mid} | error={exc}")
            return None

    async def parse_status(self, weibo_id: str) -> Sequence[ContentItem]:
        status = await self._fetch_status(weibo_id)
        include_comments = self.get_config_value("include_comments", "true").lower() == "true"
        return await self._build_status_result(status, include_comments=include_comments)

    async def _build_status_result(
        self,
        status: WeiboStatus,
        *,
        include_comments: bool,
    ) -> Sequence[ContentItem]:
        text = await self._long_text(status) or clean_status_text(status.html_text, status.raw_text)
        contents = self._status_contents(status)
        author = self._author(status)
        comments = await self._fetch_comments(status) if include_comments else ()
        repost = await self._build_repost_card(status)
        timestamp = format_weibo_timestamp(status.timestamp)
        metrics = _metrics(
            ("转发", status.reposts_count),
            ("评论", status.comments_count),
            ("点赞", status.attitudes_count),
        )
        if status.video is not None:
            await self.send_info(
                title=status.video.title or "微博视频",
                card_type="video",
                author=author,
                cover_url=status.video.cover_url or None,
                video_duration=f"{int(status.video.duration)}秒" if status.video.duration > 0 else None,
                text=text,
                url=status.url,
                timestamp=timestamp,
                metrics=metrics,
                comments=comments,
                repost=repost,
            )
        else:
            await self.send_info(
                title="微博动态",
                card_type="common",
                author=author,
                cover_url=self._cover(status),
                text=text,
                url=status.url,
                timestamp=timestamp,
                metrics=metrics,
                comments=comments,
                repost=repost,
                blocks=_status_blocks(status, limit=self.card_image_limit),
            )
        return self.result(contents)


def _status_blocks(status: WeiboStatus, *, limit: int) -> tuple[CardBlock, ...]:
    """微博动态正文常先图后文，卡片按原始顺序渲染图文。"""
    if not status.pics:
        return ()
    return build_card_blocks(
        [("image", pic.image_url) for pic in status.pics],
        limit=limit,
    )

    async def _build_repost_card(self, status: WeiboStatus) -> PreviewCard | None:
        if status.retweeted is None or not bool(self.get_config_value("include_reposts", "true").lower() == "true"):
            return None
        retweeted = status.retweeted
        retweet_text = clean_status_text(retweeted.html_text, retweeted.raw_text)
        nested_repost = await self._build_repost_card(retweeted)
        return PreviewCard(
            platform_name=self.platform.name,
            platform_display_name=self.platform.display_name,
            title="转发微博",
            text=retweet_text,
            author=self._author(retweeted),
            timestamp=format_weibo_timestamp(retweeted.timestamp),
            source_url=retweeted.url,
            cover_url=self._cover(retweeted),
            repost=nested_repost,
        )

    def _status_contents(self, status: WeiboStatus) -> list[ContentItem]:
        contents: list[ContentItem] = []
        for pic in status.pics:
            if pic.is_live and pic.live_video_url:
                contents.append(
                    self.create_live_photo_content(
                        pic.live_video_url,
                        pic.image_url,
                        cover=None,
                        headers=self.media_headers,
                        proxy=self.proxy,
                    )
                )
            else:
                contents.extend(
                    self.create_image_contents([pic.image_url], headers=self.media_headers, proxy=self.proxy)
                )
        if status.video is not None:
            contents.append(
                self.create_video_content(
                    status.video.url,
                    duration=status.video.duration,
                    headers=self.media_headers,
                    proxy=self.proxy,
                    fallback_urls=status.video.fallback_urls,
                )
            )
        if self.get_config_value("include_reposts", "true").lower() == "true":
            if status.retweeted is not None:
                contents.extend(self._status_contents(status.retweeted))
        return contents

    @staticmethod
    def _cover(status: WeiboStatus) -> str | None:
        if status.video is not None and status.video.cover_url:
            return status.video.cover_url
        if status.pics:
            return status.pics[0].image_url
        return None

    def _author(self, status: WeiboStatus) -> Author:
        return self.create_author(
            status.author.name,
            status.author.avatar_url or None,
            description=status.author.description or None,
            uid=status.author.uid or None,
        )

    async def _fetch_comments(self, status: WeiboStatus) -> tuple[Comment, ...]:
        limit = comment_limit(self.get_config_value("comment_limit", "5"))
        if limit == 0:
            return ()
        try:
            payload = await self._request_json(
                "https://m.weibo.cn/comments/hotflow",
                params={"mid": status.mid},
                headers={
                    "Referer": f"https://m.weibo.cn/detail/{status.mid}",
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                },
                error="获取微博评论失败",
            )
            items, _ = decode_comments(payload)
        except ParseException as exc:
            logger.warning(f"[微博] 评论获取失败 | mid={status.mid} | error={exc}")
            return ()
        results: list[Comment] = []
        for item in items[:limit]:
            comment_obj = self._comment(item)
            if comment_obj is not None:
                results.append(comment_obj)
        return tuple(results)

    def _comment(self, item: dict[str, object]) -> Comment | None:
        raw_user = item.get("user")
        user = raw_user if isinstance(raw_user, dict) else {}
        text = comment_text(item.get("text"))
        if not user or not text:
            return None
        author_name = str(user.get("screen_name") or "微博用户")
        avatar_url = str(user.get("profile_image_url") or "") or None

        replies: list[CommentReply] = []
        raw_replies = item.get("comments")
        if isinstance(raw_replies, list):
            for raw in raw_replies[:3]:
                if isinstance(raw, dict):
                    rep_user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
                    rep_text = comment_text(raw.get("text"))
                    if rep_text:
                        replies.append(
                            CommentReply(
                                author_name=str(rep_user.get("screen_name") or "微博用户"),
                                text=rep_text,
                                avatar_url=str(rep_user.get("profile_image_url") or "") or None,
                            )
                        )
        ts = parse_weibo_timestamp(item.get("created_at"))
        return Comment(
            author_name=author_name,
            text=text,
            avatar_url=avatar_url,
            location=str(item.get("source") or "") or None,
            timestamp=str(ts) if ts > 0 else None,
            like_count=_count(item.get("like_count")),
            reply_count=_count(item.get("total_number")),
            replies=tuple(replies),
        )

    async def parse_video(self, fid: str) -> Sequence[ContentItem]:
        payload = await self._request_json(
            f"https://h5.video.weibo.com/api/component?page=/show/{fid}",
            data={
                "data": json.dumps(
                    {"Component_Play_Playinfo": {"oid": fid}},
                    ensure_ascii=False,
                )
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            error="获取微博视频信息失败",
        )
        raw_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        comp_data = raw_data.get("Component_Play_Playinfo") if isinstance(raw_data, dict) else {}
        data = comp_data if isinstance(comp_data, dict) else {}
        if not data:
            raise ParseException("微博视频信息为空")
        urls = data.get("urls") if isinstance(data.get("urls"), dict) else {}
        candidates = [str(value) for value in urls.values() if isinstance(value, str) and value]
        if not candidates and isinstance(data.get("stream_url"), str):
            candidates.append(str(data["stream_url"]))
        if not candidates:
            raise ParseException("未找到可用的微博视频流")
        candidates = [f"https:{url}" if url.startswith("//") else url for url in candidates]
        cover = str(data.get("cover_image") or "")
        if cover.startswith("//"):
            cover = f"https:{cover}"
        try:
            duration = float(str(data.get("duration_time") or 0))
        except (TypeError, ValueError):
            duration = 0.0
        author = self.create_author(
            str(data.get("author") or "微博用户"),
            str(data.get("avatar") or "") or None,
            uid=str(data.get("author_id") or "") or None,
        )
        text = comment_text(data.get("text"))
        ts = parse_weibo_timestamp(data.get("real_date"))
        timestamp = format_weibo_timestamp(ts)
        await self.send_info(
            title=str(data.get("title") or "") or "微博视频",
            card_type="video",
            author=author,
            cover_url=cover or None,
            video_duration=f"{int(duration)}秒" if duration > 0 else None,
            text=text,
            url=f"https://video.weibo.com/show?fid={fid}",
            timestamp=timestamp,
        )
        return self.result(
            [
                self.create_video_content(
                    candidates[0],
                    duration=duration,
                    headers=self.media_headers,
                    proxy=self.proxy,
                    fallback_urls=candidates[1:],
                )
            ]
        )

    async def parse_article(self, article_id: str) -> Sequence[ContentItem]:
        payload = await self._request_json(
            "https://card.weibo.com/article/m/aj/detail",
            params={"id": article_id},
            headers={"Referer": "https://weibo.com/"},
            error="获取微博专栏失败",
        )
        if str(payload.get("msg") or "").lower() != "success":
            raise ParseException("微博专栏不存在")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if not data:
            raise ParseException("微博专栏内容为空")
        title = str(data.get("title") or "") or "微博文章"
        html_content = str(data.get("content") or "")
        blocks, text = parse_article_blocks(html_content)
        contents: list[ContentItem] = []
        for kind, value in blocks:
            if kind == "image" and value:
                contents.extend(self.create_image_contents([value], headers=self.media_headers, proxy=self.proxy))
        user = data.get("userinfo") if isinstance(data.get("userinfo"), dict) else {}
        author = self.create_author(
            str(user.get("screen_name") or "微博用户"),
            str(user.get("profile_image_url") or "") or None,
            uid=str(user.get("id") or "") or None,
        )
        metrics = _metrics(("阅读", data.get("read_count")))
        cover = next((value for kind, value in blocks if kind == "image"), None)
        timestamp = format_weibo_timestamp(parse_weibo_timestamp(data.get("create_at_unix")))
        await self.send_info(
            title=title,
            card_type="common",
            author=author,
            cover_url=cover,
            text=text,
            url=str(data.get("url") or f"https://weibo.com/ttarticle/p/show?id={article_id}"),
            metrics=metrics,
            timestamp=timestamp,
            blocks=build_card_blocks(
                [(kind, value or "") for kind, value in blocks],
                limit=self.card_image_limit,
            ),
        )
        return self.result(contents)

    @staticmethod
    def _base62_encode(number: int) -> str:
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if number == 0:
            return "0"
        result = ""
        while number > 0:
            result = alphabet[number % 62] + result
            number //= 62
        return result

    def _mid2id(self, mid: str) -> str:
        from math import ceil

        reversed_mid = str(mid)[::-1]
        size = ceil(len(reversed_mid) / 7)
        chunks: list[str] = []
        for index in range(size):
            chunk = reversed_mid[index * 7 : (index + 1) * 7][::-1]
            encoded = self._base62_encode(int(chunk))
            if index < size - 1 and len(encoded) < 4:
                encoded = "0" * (4 - len(encoded)) + encoded
            chunks.append(encoded)
        chunks.reverse()
        return "".join(chunks)
