"""酷安镜像页面解码、正文清洗和评论转换。"""

from __future__ import annotations

import re
import html
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from collections.abc import Sequence

import msgspec
from bs4 import Tag, BeautifulSoup
from bs4.element import Comment as HtmlComment, PageElement, NavigableString

from .models import (
    CoolapkFeed,
    CoolapkComment,
    CoolapkFeedPage,
    CoolapkReplyPage,
)
from ...utils.utils import format_num
from ...render.models import Comment, CommentReply
from ...utils.exceptions import ParseException

_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*(.*?)\s*</script>',
    re.IGNORECASE | re.DOTALL,
)
_NEWLINE_TAGS = frozenset({"br", "p", "div", "li", "blockquote"})
_IGNORED_TAGS = frozenset({"script", "style", "noscript"})
_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_BROKEN_NBSP_RE = re.compile(r"(?m)^\s*xa0\s*$")
_MEDIA_HOSTS = frozenset({"coolapk.com", "coolapk1s.com"})


def _extract_next_data(page_html: str) -> str:
    matched = _NEXT_DATA_RE.search(page_html)
    if matched is None:
        raise ParseException("未找到酷安页面数据")
    return matched.group(1)


def decode_feed_page(page_html: str) -> CoolapkFeedPage:
    """解码动态页中的 Next.js 服务端数据。"""
    raw = _extract_next_data(page_html)
    try:
        return msgspec.json.decode(raw.encode("utf-8"), type=CoolapkFeedPage)
    except (msgspec.DecodeError, TypeError, ValueError) as exc:
        raise ParseException("酷安动态数据格式异常") from exc


def decode_reply_page(page_html: str) -> CoolapkReplyPage:
    """解码评论页中的 Next.js 服务端数据。"""
    raw = _extract_next_data(page_html)
    try:
        return msgspec.json.decode(raw.encode("utf-8"), type=CoolapkReplyPage)
    except (msgspec.DecodeError, TypeError, ValueError) as exc:
        raise ParseException("酷安评论数据格式异常") from exc


def _attribute_text(value: object) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    if value is None:
        return ""
    return str(value)


def _anchor_text(element: Tag, base_url: str) -> str:
    if element.find("img") is not None:
        return ""
    label = element.get_text(" ", strip=True)
    href = _attribute_text(element.get("href"))
    if not href or href.startswith("#"):
        return label
    resolved = urljoin(base_url, href)
    return f"{label} ({resolved})" if label else resolved


def _walk_html(node: BeautifulSoup | PageElement, parts: list[str]) -> None:
    if isinstance(node, HtmlComment):
        return
    if isinstance(node, NavigableString):
        parts.append(html.unescape(str(node)))
        return
    if not isinstance(node, Tag) or node.name in _IGNORED_TAGS:
        return
    if node.name in _NEWLINE_TAGS:
        parts.append("\n")
    if node.name == "img":
        parts.append(_attribute_text(node.get("alt")))
        return
    if node.name == "a":
        parts.append(_anchor_text(node, "https://www.coolapk.com/"))
        return
    for child in node.children:
        _walk_html(child, parts)


def html_to_text(value: str) -> str:
    """把酷安富文本转换为保留段落结构的纯文本。"""
    if not value.strip():
        return ""
    soup = BeautifulSoup(value, "html.parser")
    parts: list[str] = []
    _walk_html(soup, parts)
    text = "".join(parts).replace("\u200b", "").replace("\xa0", " ")
    text = _BROKEN_NBSP_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _MULTI_NEWLINE_RE.sub("\n\n", text).strip()


def feed_text(feed: CoolapkFeed) -> str:
    """返回主帖正文。"""
    return html_to_text(feed.message)


def normalize_media_url(value: str) -> str:
    """规范化媒体地址，只接受酷安及其镜像域名。"""
    current = html.unescape(value).strip()
    if current.startswith("//"):
        current = f"https:{current}"
    elif current.startswith("http://"):
        current = f"https://{current[7:]}"
    if not current.startswith("https://"):
        return ""
    parsed = urlsplit(current)
    host = (parsed.hostname or "").lower()
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in _MEDIA_HOSTS):
        return ""
    return current


def normalize_media_urls(values: Sequence[str]) -> list[str]:
    """规范化并去重一组媒体地址。"""
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_media_url(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        results.append(normalized)
    return results


def format_timestamp(value: int | None) -> str | None:
    """把秒级时间戳转换为卡片显示文本。"""
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def _metric_count(value: int | None) -> str | None:
    if value is None or value <= 0:
        return None
    return format_num(value)


def _comment_text(message: str, picture_count: int) -> str:
    text = html_to_text(message)
    marker = f"[图片 x{picture_count}]" if picture_count > 0 else ""
    if text and marker:
        return f"{text}\n\n{marker}"
    return text or marker


def build_comments(
    comments: Sequence[CoolapkComment],
    *,
    limit: int,
    reply_limit: int = 3,
) -> tuple[Comment, ...]:
    """把镜像评论转换为公共卡片评论模型。"""
    results: list[Comment] = []
    for item in comments[:limit]:
        pictures = normalize_media_urls(item.pic_arr or [])
        text = _comment_text(item.message, len(pictures))
        if not text:
            continue
        replies: list[CommentReply] = []
        for reply in item.reply_rows[:reply_limit]:
            reply_pictures = normalize_media_urls(reply.pic_arr or [])
            reply_text = _comment_text(reply.message, len(reply_pictures))
            if not reply_text:
                continue
            replies.append(
                CommentReply(
                    author_name=reply.username or "酷安用户",
                    text=reply_text,
                    parent_author=reply.rusername or None,
                )
            )
        results.append(
            Comment(
                author_name=item.username or "酷安用户",
                text=text,
                avatar_url=normalize_media_url(item.user_avatar) or None,
                timestamp=format_timestamp(item.dateline),
                like_count=_metric_count(item.likenum),
                reply_count=_metric_count(item.replynum),
                replies=tuple(replies),
            )
        )
    return tuple(results)
