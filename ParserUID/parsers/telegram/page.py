"""Telegram 公开网页 DOM 解析与结构化内容提取。"""

from __future__ import annotations

import re
import html
from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from .models import (
    TelegramPoll,
    TelegramPost,
    TelegramMedia,
    TelegramReply,
    TelegramButton,
    TelegramForward,
    TelegramPreview,
    TelegramReaction,
    TelegramPollOption,
)

MEDIA_SELECTOR = ",".join(
    (
        ".tgme_widget_message_photo_wrap",
        ".tgme_widget_message_video_player",
        ".tgme_widget_message_voice_player",
        ".tgme_widget_message_roundvideo_player",
        ".tgme_widget_message_sticker_wrap",
        ".tgme_widget_message_document_wrap",
        ".tgme_widget_message_document_title",
        ".tgme_widget_message_audio_player",
        ".tgme_widget_message_audio",
    )
)
BACKGROUND_IMAGE_RE = re.compile(
    r"""background-image\s*:\s*url\(\s*(['"]?)(.*?)\1\s*\)""",
    re.IGNORECASE | re.DOTALL,
)
REPLY_TARGET_RE = re.compile(r"/(\d+)(?:[/?#]|$)")
COUNT_RE = re.compile(r"\d[\d.,]*(?:\s*[KMB万亿])?", re.IGNORECASE)
DIRECT_FILE_SUFFIXES = (
    ".7z",
    ".apk",
    ".doc",
    ".docx",
    ".epub",
    ".pdf",
    ".rar",
    ".tar",
    ".txt",
    ".xls",
    ".xlsx",
    ".zip",
)
TRUSTED_MEDIA_SUFFIXES = (
    "telesco.pe",
    "telegram.org",
    "telegram-cdn.org",
    "telegram-cdn.com",
    "t.me",
)


def parse_page_html(
    html_text: str,
    *,
    channel: str,
    post_id: int,
) -> TelegramPost | None:
    """解析 Telegram 公开帖子页并只返回请求的那条消息。"""
    soup = BeautifulSoup(html_text, "html.parser")
    channel_info = _parse_channel_info(soup, channel)
    message = _find_message(soup, post_id)
    if message is None:
        return None
    return _parse_post(
        message,
        channel=channel,
        post_id=post_id,
        channel_info=channel_info,
    )


def _find_message(soup: BeautifulSoup, post_id: int) -> Tag | None:
    for message in soup.select(".tgme_widget_message"):
        data_post = _attr(message, "data-post")
        if data_post and data_post.rsplit("/", 1)[-1] == str(post_id):
            return message
    return None


def _parse_channel_info(
    soup: BeautifulSoup,
    channel: str,
) -> dict[str, str | None]:
    owner = _first_text(soup, ".tgme_widget_message_owner_name")
    title = _first_text(soup, ".tgme_channel_info_header_title") or owner or _meta(soup, "property", "og:title")
    description = _first_text(soup, ".tgme_channel_info_description")
    if not description and soup.select_one(".tgme_channel_info") is not None:
        description = _meta(soup, "property", "og:description")
    username = _first_text(soup, ".tgme_channel_info_header_username a") or ""
    if username.startswith("@"):
        username = username[1:]
    avatar = _first_image_url(
        soup,
        (
            ".tgme_widget_message_user_photo img",
            ".tgme_channel_info_header .tgme_page_photo_image img",
            ".tgme_page_photo_image img",
        ),
    )
    if not avatar:
        avatar = _first_background_url(
            soup,
            (
                ".tgme_widget_message_user_photo",
                ".tgme_channel_info_header .tgme_page_photo_image",
                ".tgme_page_photo_image",
            ),
        )
    if not avatar and soup.select_one(".tgme_channel_info") is not None:
        avatar = _meta(soup, "property", "og:image")
    return {
        "username": username or channel,
        "title": title,
        "description": description,
        "avatar": avatar,
    }


def _parse_post(
    message: Tag,
    *,
    channel: str,
    post_id: int,
    channel_info: dict[str, str | None],
) -> TelegramPost:
    bubble = message.select_one(".tgme_widget_message_bubble") or message
    text = _post_text(bubble)
    media = _parse_media(bubble)
    preview = _parse_preview(message)
    reply = _parse_reply(message)
    forwarded = _parse_forward(message)
    poll = _parse_poll(bubble)
    buttons = _parse_buttons(message)
    reactions = _parse_reactions(message)
    views = _first_text(bubble, ".tgme_widget_message_views")
    meta_text = _first_text(bubble, ".tgme_widget_message_meta")
    timestamp = _parse_timestamp(_first_attr(bubble, ".tgme_widget_message_date time", "datetime"))
    owner = _first_text(message, ".tgme_widget_message_owner_name")
    signature = _first_text(message, ".tgme_widget_message_from_author")
    author = signature or owner or channel_info["title"] or channel
    unsupported = bool(
        bubble.select_one(".message_media_not_supported_wrap") or any(item.available is False for item in media)
    )
    return TelegramPost(
        channel=str(channel_info["username"] or channel),
        post_id=post_id,
        channel_title=str(channel_info["title"] or author),
        channel_avatar=_absolute_url(channel_info["avatar"]),
        channel_description=channel_info["description"],
        author=author,
        text=text,
        timestamp=timestamp,
        views=views,
        edited=bool(meta_text and meta_text.startswith("edited")),
        media=tuple(media),
        preview=preview,
        reply=reply,
        forwarded=forwarded,
        poll=poll,
        buttons=buttons,
        reactions=reactions,
        unsupported_media=unsupported,
    )


def _post_text(bubble: Tag) -> str | None:
    node = bubble.select_one(".tgme_widget_message_text.js-message_text")
    if node is None:
        for candidate in bubble.select(".tgme_widget_message_text"):
            if "js-message_reply_text" not in _class_tokens(candidate):
                node = candidate
                break
    return _clean_text(node.get_text("\n", strip=True)) if node is not None else None


def _parse_media(bubble: Tag) -> list[TelegramMedia]:
    media: list[TelegramMedia] = []
    seen_nodes: set[int] = set()
    seen_items: set[tuple[str, str | None]] = set()
    owner_message = _closest_message(bubble)
    owner_post = _attr(owner_message, "data-post")
    for node in bubble.select(MEDIA_SELECTOR):
        media_message = _closest_message(node)
        if owner_post and media_message is not None and _attr(media_message, "data-post") != owner_post:
            continue
        identity = id(node)
        if identity in seen_nodes:
            continue
        seen_nodes.add(identity)
        classes = _class_tokens(node)
        first_class = classes[0] if classes else ""
        item: TelegramMedia | None
        if first_class == "tgme_widget_message_photo_wrap":
            item = TelegramMedia(
                kind="image",
                url=_background_url(_attr(node, "style")),
            )
        elif first_class == "tgme_widget_message_video_player":
            item = _parse_video(node)
        elif first_class == "tgme_widget_message_voice_player":
            item = _parse_voice(node)
        elif first_class == "tgme_widget_message_roundvideo_player":
            item = _parse_roundvideo(node)
        elif first_class == "tgme_widget_message_sticker_wrap":
            item = _parse_sticker(node)
        elif "tgme_widget_message_document_title" in classes:
            item = _parse_document(node)
        elif "tgme_widget_message_audio" in classes:
            item = _parse_audio(node)
        else:
            item = None
        if item is None:
            continue
        key = (item.kind, item.url)
        if key in seen_items:
            continue
        seen_items.add(key)
        media.append(item)
    return media


def _parse_video(node: Tag) -> TelegramMedia:
    video = node.select_one("video.tgme_widget_message_video") or node.select_one("video")
    url = _absolute_url(_attr(video, "src"))
    thumb = _background_url(_first_attr(node, ".tgme_widget_message_video_thumb", "style"))
    duration = _duration(_first_text(node, "time.message_video_duration"))
    return TelegramMedia(
        kind="video" if duration else "gif",
        url=url,
        thumb=thumb,
        duration=duration,
        available=url is not None,
    )


def _parse_voice(node: Tag) -> TelegramMedia:
    audio = node.select_one("audio.tgme_widget_message_voice") or node.select_one("audio")
    url = _absolute_url(_attr(audio, "src"))
    return TelegramMedia(
        kind="voice",
        url=url,
        duration=_duration(_first_text(node, "time.tgme_widget_message_voice_duration")),
        available=url is not None,
    )


def _parse_roundvideo(node: Tag) -> TelegramMedia:
    video = node.select_one("video.tgme_widget_message_roundvideo") or node.select_one("video")
    url = _absolute_url(_attr(video, "src"))
    return TelegramMedia(
        kind="roundvideo",
        url=url,
        thumb=_first_background_url(
            node,
            (".tgme_widget_message_roundvideo_thumb",),
        ),
        duration=_duration(_first_text(node, "time.tgme_widget_message_roundvideo_duration")),
        available=url is not None,
    )


def _parse_sticker(node: Tag) -> TelegramMedia | None:
    candidates = (
        ("picture.tgme_widget_message_tgsticker source", "srcset"),
        ("i.tgme_widget_message_sticker", "data-webp"),
        ("video.js-videosticker_video", "src"),
    )
    for selector, attribute in candidates:
        source = node.select_one(selector)
        if source is None:
            continue
        raw = _attr(source, attribute)
        if attribute == "srcset":
            raw = _first_srcset_url(raw)
        url = _absolute_url(raw)
        if url is None:
            continue
        path = urlparse(url).path.lower()
        # 动图贴纸要走动态媒体内容，静态 TGS 不具备当前发送层可用的动画格式。
        if path.endswith((".webm", ".mp4")):
            kind = "dynamic"
        elif path.endswith(".tgs"):
            kind = "file"
        else:
            kind = "image"
        thumb = _absolute_url(_first_attr(source, "img", "src"))
        return TelegramMedia(kind=kind, url=url, thumb=thumb)
    return None


def _parse_document(node: Tag) -> TelegramMedia:
    if "tgme_widget_message_document_title" in _class_tokens(node):
        parent = node.parent if isinstance(node.parent, Tag) else None
        while parent is not None and not (
            parent.name == "a"
            or "tgme_widget_message_document_wrap" in _class_tokens(parent)
            or "tgme_widget_message_document" in _class_tokens(parent)
        ):
            parent = parent.parent if isinstance(parent.parent, Tag) else None
        if parent is not None:
            node = parent
    link = node if node.name == "a" else node.select_one("a[href]")
    if link is None:
        # 部分公开页把可下载文件链接放在文档容器自身或直接作为 href 属性。
        link = node if _attr(node, "href") else None
    if link is None:
        parent: Tag | None = node.parent if isinstance(node.parent, Tag) else None
        while parent is not None and parent.name != "a":
            parent = parent.parent if isinstance(parent.parent, Tag) else None
        link = parent
    href = _absolute_url(_attr(link, "href"))
    url = href if href is not None and _is_direct_file_url(href) else None
    name = _first_text(node, ".tgme_widget_message_document_title") or _first_text(
        node, ".tgme_widget_message_document_extra"
    )
    return TelegramMedia(
        kind="file",
        url=url,
        name=name,
        available=url is not None,
    )


def _parse_audio(node: Tag) -> TelegramMedia | None:
    audio = node if node.name == "audio" else node.select_one("audio[src]")
    if audio is None:
        return None
    url = _absolute_url(_attr(audio, "src"))
    if url is None:
        return None
    return TelegramMedia(
        kind="audio",
        url=url,
        duration=_duration(_first_text(node, "time")),
    )


def _parse_preview(message: Tag) -> TelegramPreview | None:
    preview = message.select_one(".tgme_widget_message_link_preview")
    if preview is None:
        return None
    thumb = _first_background_url(
        preview,
        (
            ".link_preview_image",
            ".link_preview_right_image",
            ".link_preview_square_image",
        ),
    )
    return TelegramPreview(
        url=_absolute_url(_attr(preview, "href")),
        title=_first_text(preview, ".link_preview_title"),
        site_name=_first_text(preview, ".link_preview_site_name"),
        description=_first_text(preview, ".link_preview_description"),
        thumb=thumb,
    )


def _parse_reply(message: Tag) -> TelegramReply | None:
    reply = message.select_one(".tgme_widget_message_reply")
    if reply is None:
        return None
    text = (
        _first_text(reply, ".tgme_widget_message_text.js-message_reply_text")
        or _first_text(reply, ".tgme_widget_message_metatext")
        or _first_text(reply, ".tgme_widget_message_text")
    )
    name = _first_text(reply, ".tgme_widget_message_author_name") or _first_text(reply, ".tgme_widget_message_author")
    if not text:
        return None
    href = _absolute_url(_attr(reply, "href"))
    target = REPLY_TARGET_RE.search(href or "")
    return TelegramReply(
        name=name or "引用消息",
        text=text,
        url=href,
        to_message=int(target.group(1)) if target is not None else None,
        thumb=_first_background_url(
            reply,
            (".tgme_widget_message_reply_thumb",),
        ),
    )


def _parse_forward(message: Tag) -> TelegramForward | None:
    container = message.select_one(".tgme_widget_message_forwarded_from")
    if container is None:
        return None
    forwarded = container.select_one(".tgme_widget_message_forwarded_from_name")
    name = _clean_text(forwarded.get_text(" ", strip=True)) if forwarded else None
    url = _absolute_url(_attr(forwarded, "href")) or _absolute_url(_attr(container, "href"))
    if not name:
        matched = re.search(
            r"(?:t\.me|telegram\.me|telegram\.dog)/"
            r"([A-Za-z][A-Za-z0-9_]{2,31})",
            url or "",
            re.IGNORECASE,
        )
        name = matched.group(1) if matched is not None else "未知来源"
    return TelegramForward(
        name=name,
        url=url,
        author=_first_text(
            message,
            ".tgme_widget_message_forwarded_from_author",
        ),
    )


def _parse_poll(bubble: Tag) -> TelegramPoll | None:
    poll = bubble.select_one(".tgme_widget_message_poll")
    if poll is None:
        return None
    options: list[TelegramPollOption] = []
    for option in poll.select(".tgme_widget_message_poll_option"):
        name = _first_text(option, ".tgme_widget_message_poll_option_text")
        percent = _first_text(option, ".tgme_widget_message_poll_option_percent")
        if not name or not percent:
            continue
        matched = re.search(r"\d+", percent)
        if matched is not None:
            options.append(
                TelegramPollOption(
                    name=name,
                    percent=int(matched.group(0)),
                )
            )
    return TelegramPoll(
        question=_first_text(poll, ".tgme_widget_message_poll_question") or "",
        kind=_first_text(poll, ".tgme_widget_message_poll_type"),
        votes=_first_text(bubble, ".tgme_widget_message_voters"),
        options=tuple(options),
    )


def _parse_buttons(message: Tag) -> tuple[TelegramButton, ...]:
    buttons: list[TelegramButton] = []
    for button in message.select(".tgme_widget_message_inline_button"):
        title = _first_text(button, "span") or _clean_text(button.get_text(" ", strip=True))
        if not title:
            continue
        buttons.append(
            TelegramButton(
                title=title,
                url=_absolute_url(_attr(button, "href")),
            )
        )
    return tuple(buttons)


def _parse_reactions(message: Tag) -> tuple[TelegramReaction, ...]:
    reactions: list[TelegramReaction] = []
    for node in message.select(".tgme_widget_message_reactions .tgme_reaction"):
        full_text = _clean_text(node.get_text(" ", strip=True)) or ""
        count = _last_child_text(node)
        if not count:
            matched = COUNT_RE.search(full_text)
            count = matched.group(0) if matched is not None else ""
        if not count:
            continue
        classes = _class_tokens(node)
        emoji_node = node.select_one("i.emoji b")
        custom_emoji = node.select_one("tg-emoji")
        if "tgme_reaction_paid" in classes:
            label, kind = "⭐", "telegram_stars"
        elif emoji_node is not None:
            label = _clean_text(emoji_node.get_text("", strip=True)) or "表情"
            kind = "emoji"
        elif custom_emoji is not None:
            label = _clean_text(custom_emoji.get_text("", strip=True)) or "表情"
            kind = "custom_emoji"
        else:
            label, kind = "互动", "unknown"
        reactions.append(TelegramReaction(label=label, count=count, kind=kind))
    return tuple(reactions)


def _duration(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        parts = [int(part) for part in value.strip().split(":")]
    except ValueError:
        return 0.0
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    if len(parts) == 3:
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    return 0.0


def _parse_timestamp(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    text = html.unescape(value).replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "").replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    cleaned = text.strip()
    return cleaned or None


def _first_text(node: Tag | BeautifulSoup, selector: str) -> str | None:
    matched = node.select_one(selector)
    if matched is None:
        return None
    # Telegram 页面常在容器内嵌图标、按钮和隐藏说明，只取直接文本与标题节点。
    text = matched.get_text(" ", strip=False)
    return _clean_text(text)


def _first_attr(
    node: Tag | BeautifulSoup,
    selector: str,
    attribute: str,
) -> str | None:
    matched = node.select_one(selector)
    return _attr(matched, attribute) or None


def _attr(node: Tag | None, name: str) -> str:
    if node is None:
        return ""
    value = node.get(name)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(item for item in value if isinstance(item, str)).strip()
    return ""


def _class_tokens(node: Tag) -> list[str]:
    value = node.get("class")
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _closest_message(node: Tag) -> Tag | None:
    current: Tag | None = node
    while current is not None:
        if "tgme_widget_message" in _class_tokens(current):
            return current
        current = current.parent if isinstance(current.parent, Tag) else None
    return None


def _meta(
    soup: BeautifulSoup,
    attribute: str,
    value: str,
) -> str | None:
    for node in soup.select("meta"):
        if _attr(node, attribute).lower() == value.lower():
            return _clean_text(_attr(node, "content"))
    return None


def _first_image_url(
    node: Tag | BeautifulSoup,
    selectors: tuple[str, ...],
) -> str | None:
    for selector in selectors:
        matched = node.select_one(selector)
        if matched is None:
            continue
        url = _absolute_url(_attr(matched, "src"))
        if url is not None:
            return url
    return None


def _first_background_url(
    node: Tag | BeautifulSoup,
    selectors: tuple[str, ...],
) -> str | None:
    for selector in selectors:
        matched = node.select_one(selector)
        if matched is None:
            continue
        url = _background_url(_attr(matched, "style"))
        if url is not None:
            return url
    return None


def _background_url(style: str | None) -> str | None:
    if not style:
        return None
    matched = BACKGROUND_IMAGE_RE.search(style)
    return _absolute_url(matched.group(2)) if matched is not None else None


def _first_srcset_url(value: str) -> str:
    candidate = value.split(",", 1)[0].strip()
    return candidate.split(" ", 1)[0] if candidate else ""


def _absolute_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = html.unescape(value).strip()
    if text.startswith("//"):
        text = f"https:{text}"
    if not text.startswith(("http://", "https://")):
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if not parsed.hostname or parsed.username or parsed.password:
        return None
    return text


def _is_direct_file_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in TRUSTED_MEDIA_SUFFIXES):
        return False
    path = parsed.path.lower()
    return path.startswith("/file/") or path.endswith(DIRECT_FILE_SUFFIXES)


def _last_child_text(node: Tag) -> str | None:
    children = node.find_all(recursive=False)
    if not children:
        return None
    last = children[-1]
    return _clean_text(last.get_text(" ", strip=True))
