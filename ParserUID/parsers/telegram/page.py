"""Telegram 页面 DOM 解析与内容提取（支持 BeautifulSoup 降级）。"""

from __future__ import annotations

import re
import html
from datetime import datetime

from bs4 import BeautifulSoup

from .models import (
    TelegramPost,
    TelegramMedia,
)

BACKGROUND_IMAGE_RE = re.compile(
    r"""background-image\s*:\s*url\(\s*(['"]?)(.*?)\1\s*\)""",
    re.IGNORECASE | re.DOTALL,
)
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
TRUSTED_MEDIA_SUFFIXES = ("telesco.pe", "telegram.org", "t.me")


def _clean_text(value: str) -> str:
    text = html.unescape(value).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", text)


def _extract_bg_url(style_attr: str) -> str | None:
    if not style_attr:
        return None
    match = BACKGROUND_IMAGE_RE.search(style_attr)
    return match.group(2) if match else None


def parse_page_html(
    html_text: str,
    *,
    channel: str,
    post_id: int,
) -> TelegramPost | None:
    soup = BeautifulSoup(html_text, "html.parser")
    target_msg = None
    for msg in soup.select(".tgme_widget_message"):
        data_post = str(msg.get("data-post") or "")
        if data_post.rsplit("/", 1)[-1] == str(post_id):
            target_msg = msg
            break
    if target_msg is None:
        target_msg = soup.select_one(".tgme_widget_message")
    if target_msg is None:
        return None

    # 提取频道信息
    owner_node = soup.select_one(".tgme_widget_message_owner_name")
    ch_title = owner_node.get_text(strip=True) if owner_node else channel
    ch_desc_node = soup.select_one(".tgme_channel_info_description")
    ch_desc = ch_desc_node.get_text(strip=True) if ch_desc_node else None

    # 提取正文
    text_node = target_msg.select_one(".tgme_widget_message_text")
    post_text = text_node.get_text("\n", strip=True) if text_node else ""

    # 提取媒体
    medias: list[TelegramMedia] = []
    for photo_wrap in target_msg.select(".tgme_widget_message_photo_wrap"):
        style = str(photo_wrap.get("style") or "")
        bg_url = _extract_bg_url(style)
        if bg_url:
            medias.append(TelegramMedia(kind="image", url=bg_url, thumb=bg_url))

    for video_wrap in target_msg.select(".tgme_widget_message_video_player"):
        vid_tag = video_wrap.select_one("video")
        vid_url = str(vid_tag.get("src") or "") if vid_tag else ""
        thumb_node = video_wrap.select_one(".tgme_widget_message_video_thumb")
        thumb_url = _extract_bg_url(str(thumb_node.get("style") or "")) if thumb_node else None
        if vid_url:
            medias.append(TelegramMedia(kind="video", url=vid_url, thumb=thumb_url))

    # 提取浏览数与发布时间
    views_node = target_msg.select_one(".tgme_widget_message_views")
    views = views_node.get_text(strip=True) if views_node else None
    time_node = target_msg.select_one(".tgme_widget_message_date time")
    dt_str = str(time_node.get("datetime") or "") if time_node else ""
    timestamp = None
    if dt_str:
        try:
            timestamp = int(datetime.fromisoformat(dt_str.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass

    return TelegramPost(
        channel=channel,
        post_id=post_id,
        channel_title=ch_title,
        channel_avatar=None,
        channel_description=ch_desc,
        author=ch_title,
        text=post_text or None,
        timestamp=timestamp,
        views=views,
        media=tuple(medias),
    )
