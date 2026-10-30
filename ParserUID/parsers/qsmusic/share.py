"""汽水音乐页面解析与歌词格式化工具。"""

from __future__ import annotations

import re

import msgspec

from .models import (
    QSTrackData,
    QSRouterData,
)
from ...utils.exceptions import ParseException

ROUTER_DATA_RE = re.compile(
    r'<script\s+async=""\s+data-script-src="modern-inline">'
    r"_ROUTER_DATA\s*=\s*({.*?});",
    flags=re.DOTALL,
)


def decode_share_html(html: str) -> QSTrackData:
    matched = ROUTER_DATA_RE.search(html)
    if matched is None:
        raise ParseException("汽水音乐分享页缺少结构化数据")
    try:
        router_data = msgspec.json.decode(matched.group(1).encode("utf-8"), type=QSRouterData)
    except msgspec.DecodeError as exc:
        raise ParseException("汽水音乐分享页数据异常") from exc

    track = router_data.loaderData.track_page.audioWithLyricsOption
    if not track.url.startswith(("http://", "https://")):
        raise ParseException("汽水音乐未返回有效音频地址")
    return track


def cover_url(track: QSTrackData) -> str | None:
    return track.coverURL if track.coverURL.startswith(("http://", "https://")) else None


def artist_avatar_url(track: QSTrackData) -> str | None:
    for artist in track.trackInfo.artists:
        if artist.medium_avatar_url and artist.medium_avatar_url.urls:
            url = artist.medium_avatar_url.urls[0]
            if url.startswith(("http://", "https://")):
                return url
        if artist.url_avatar and artist.url_avatar.urls:
            url = artist.url_avatar.urls[0]
            if url.startswith(("http://", "https://")):
                return url
    return None


def track_album(track: QSTrackData) -> str:
    return track.trackInfo.album.name


def format_lyrics(track: QSTrackData) -> str:
    lines: list[str] = []
    for sentence in track.lyrics.sentences:
        start_ms = max(0, sentence.startMs)
        text = sentence.text.strip()
        if not text and sentence.words:
            text = "".join(word.text for word in sentence.words).strip()
        if not text:
            continue
        minutes, remainder = divmod(start_ms, 60_000)
        seconds, millis = divmod(remainder, 1000)
        centiseconds = millis // 10
        lines.append(f"[{minutes:02d}:{seconds:02d}.{centiseconds:02d}]{text}")
    return "\n".join(lines)
