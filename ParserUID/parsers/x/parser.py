"""X (Twitter) 平台推文与多媒体链接解析器。"""

from __future__ import annotations

import re
import uuid
import asyncio
from typing import ClassVar
from collections.abc import Sequence

import httpx
import msgspec

from ..base import BaseParser, handle
from .cards import link_text, poll_text
from .media import tweet_contents, first_cover_url, description_contents
from .models import (
    XTweet,
    decode_response,
)
from ...sender import ContentItem
from ...utils.models import Platform
from ...render.models import Author, Metric, ImageBlock, PreviewCard
from ...utils.helpers import build_image_blocks
from ...utils.exceptions import ParseException

V2_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
SYNDICATION_TWEET_URL = "https://cdn.syndication.twimg.com/tweet-result"
TRANSLATION_URL = "https://x.com/i/api/1.1/strato/column/None/translation.json"
NON_LINGUISTIC_LANGS = frozenset({"qme", "qst", "zxx"})
STATUS_PATTERN = (
    r"(?<![A-Za-z0-9.-])(?<![/.])https?://(?:(?:www|mobile)\.)?{host}/"
    r"(?:[A-Za-z0-9_]+/)*status/(?P<tweet_id>[0-9]+)"
)


def count_text(value: int) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}".rstrip("0").rstrip(".") + "亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}".rstrip("0").rstrip(".") + "万"
    return str(value)


def should_translate(tweet: XTweet) -> bool:
    lang = tweet.lang.lower()
    if tweet.is_article or not lang or lang in NON_LINGUISTIC_LANGS or lang.split("-", 1)[0] == "zh":
        return False
    return bool(tweet.text.strip())


def _safe_error_text(status: int, reason: str) -> str:
    text = reason.strip()
    if not text or len(text) > 80:
        text = "请求失败"
    return f"X 接口请求失败：HTTP {status}（{text}）"


def _resolution(media: object) -> str | None:
    variants = getattr(media, "mp4_variants", ())
    if isinstance(variants, Sequence):
        for variant in sorted(variants, key=lambda item: getattr(item, "bitrate", 0), reverse=True):
            url = getattr(variant, "url", "")
            if isinstance(url, str) and (match := re.search(r"/(\d+)x(\d+)/", url)):
                return f"{match.group(1)} × {match.group(2)}"
    return None


class XParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="x", display_name="X")

    def _api_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Authorization": V2_BEARER,
            "Content-Type": "application/json",
            "Origin": "https://x.com",
            "Referer": "https://x.com/",
        }
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
            if "auth_token=" in cookie:
                headers["x-twitter-auth-type"] = "OAuth2Session"
        csrf = uuid.uuid4().hex
        headers.update(
            {
                "x-twitter-active-user": "yes",
                "x-twitter-client-language": "zh-cn",
                "x-csrf-token": csrf,
            }
        )
        return headers

    def _media_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            "Referer": "https://x.com/",
        }
        cookie = self.get_config_value("cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    async def _request_tweet(self, tweet_id: str) -> dict[str, object]:
        headers = {
            "Accept": "application/json",
            "Referer": "https://platform.twitter.com/",
        }
        params = {
            "id": tweet_id,
            "lang": "en",
            "token": uuid.uuid4().hex,
        }
        client = await self.get_client()
        retries = 2
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    SYNDICATION_TWEET_URL,
                    params=params,
                    headers=headers,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                if response.status_code >= 400:
                    raise ParseException(_safe_error_text(response.status_code, response.reason_phrase or ""))
                payload = msgspec.json.decode(response.content)
                if not isinstance(payload, dict):
                    raise ParseException("X 返回的数据格式异常")
                return {str(k): v for k, v in payload.items()}
            except ParseException:
                raise
            except (httpx.HTTPError, msgspec.DecodeError, TimeoutError) as exc:
                if attempt < retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                raise ParseException("获取 X 帖子详情失败") from exc
        raise ParseException("获取 X 帖子详情失败")

    async def _fetch_translation(self, tweet_id: str) -> str:
        cookie = self.get_config_value("cookie")
        if "auth_token=" not in cookie:
            return ""
        headers = self._api_headers()
        try:
            client = await self.get_client()
            response = await client.post(
                TRANSLATION_URL,
                headers=headers,
                json={
                    "content_type": "POST",
                    "id": tweet_id,
                    "dst_lang": "zh",
                },
            )
            if response.status_code >= 400:
                return ""
            payload = msgspec.json.decode(response.content)
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                return ""
            value = result.get("text")
            return value.strip() if isinstance(value, str) else ""
        except Exception:
            return ""

    async def _translation(self, tweet: XTweet) -> str:
        if not bool(self.get_config_value("translate", "false").lower() == "true"):
            return ""
        if tweet.translated_text:
            return tweet.translated_text
        if not should_translate(tweet):
            return ""
        return await self._fetch_translation(tweet.rest_id)

    def _author(self, tweet: XTweet) -> Author:
        return self.create_author(
            tweet.author.name,
            tweet.author.avatar_url or None,
            description=tweet.author.description or None,
            uid=tweet.author.screen_name or tweet.author.uid or None,
        )

    @staticmethod
    def _metrics(tweet: XTweet) -> tuple[Metric, ...]:
        values = (
            ("查看", tweet.view_count),
            ("喜欢", tweet.like_count),
            ("回复", tweet.reply_count),
            ("书签", tweet.bookmark_count),
            ("转推", tweet.quote_count + tweet.retweet_count),
        )
        return tuple(Metric(label=label, value=count_text(value)) for label, value in values if value > 0)

    def _create_image(self, url: str) -> ContentItem:
        return self.create_image_contents([url], headers=self._media_headers(), proxy=self.proxy)[0]

    def _create_video(self, url: str, **kwargs: object) -> ContentItem:
        duration_val = float(str(kwargs.get("duration", 0.0) or 0.0))
        fallback_urls = kwargs.get("fallback_urls")
        fallbacks_seq: Sequence[str] | None = list(fallback_urls) if isinstance(fallback_urls, Sequence) else None
        return self.create_video_content(
            url,
            duration=duration_val,
            headers=self._media_headers(),
            proxy=self.proxy,
            fallback_urls=fallbacks_seq,
        )

    def _create_dynamic(self, url: str) -> ContentItem:
        return self.create_dynamic_contents([url], headers=self._media_headers(), proxy=self.proxy)[0]

    def _create_graphic(self, url: str) -> ContentItem:
        return self.create_image_contents([url], headers=self._media_headers(), proxy=self.proxy)[0]

    def _image_blocks(self, tweet: XTweet) -> tuple[ImageBlock, ...]:
        """图片推文与长文图片按原顺序进入卡片，视频不重复渲染封面。"""
        urls = [media.source_url for media in tweet.media if media.kind == "photo" and media.source_url]
        if not urls:
            urls = [
                block.media.source_url
                for block in tweet.article_blocks
                if block.media is not None and block.media.kind == "photo" and block.media.source_url
            ]
        return build_image_blocks(urls, limit=self.card_image_limit)

    def _contents(self, tweet: XTweet) -> list[ContentItem]:
        return tweet_contents(
            tweet,
            create_image=self._create_image,
            create_video=self._create_video,
            create_dynamic=self._create_dynamic,
            create_graphic=self._create_graphic,
        )

    async def _build_repost_card(self, tweet: XTweet) -> PreviewCard | None:
        if tweet.quoted is None:
            return None
        quoted = tweet.quoted
        desc_lines = description_contents(quoted)
        return PreviewCard(
            platform_name=self.platform.name,
            platform_display_name=self.platform.display_name,
            title="引用推文",
            text="\n".join(desc_lines),
            author=self._author(quoted),
            source_url=quoted.source_url,
            cover_url=first_cover_url(quoted) or None,
        )

    @handle("x.com", STATUS_PATTERN.format(host=r"x\.com"))
    @handle("twitter.com", STATUS_PATTERN.format(host=r"twitter\.com"))
    async def _parse(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        tweet_id = searched.group("tweet_id")
        payload = await self._request_tweet(tweet_id)
        try:
            tweet = decode_response(payload)
        except (ValueError, TypeError, msgspec.ValidationError) as exc:
            raise ParseException("X 帖子不存在、已删除或当前不可见") from exc
        if not tweet.has_content:
            raise ParseException("X 帖子没有可解析的内容")
        if tweet.possibly_sensitive and self.get_config_value("sensitive_mode", "warn") == "skip":
            raise ParseException("该帖子被作者标记为敏感内容，已按设置跳过")

        translation = await self._translation(tweet)
        desc_lines = description_contents(tweet, translation=translation)
        if tweet.poll is not None:
            desc_lines.append(poll_text(tweet.poll))
        elif tweet.card is not None and bool(self.get_config_value("include_link_card", "true").lower() == "true"):
            desc_lines.append(link_text(tweet.card))

        repost = await self._build_repost_card(tweet)
        video_media = next(
            (media for media in tweet.media if media.kind == "video"),
            None,
        )
        cover = first_cover_url(tweet) or None
        is_video = video_media is not None

        await self.send_info(
            title=tweet.title or ("X 视频" if is_video else "X 动态"),
            card_type="video" if is_video else "common",
            author=self._author(tweet),
            cover_url=cover,
            video_duration=f"{int(video_media.duration)}秒" if (video_media and video_media.duration > 0) else None,
            video_quality=_resolution(video_media) if video_media else None,
            text="\n".join(desc_lines),
            url=tweet.source_url,
            metrics=self._metrics(tweet),
            repost=repost,
            blocks=self._image_blocks(tweet),
        )

        contents = self._contents(tweet)
        if bool(self.get_config_value("include_quoted_media", "false").lower() == "true"):
            if tweet.quoted is not None:
                contents.extend(self._contents(tweet.quoted))
        return self.result(contents)
