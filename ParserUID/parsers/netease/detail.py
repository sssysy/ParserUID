"""网易云歌曲详情与音质文件信息转换。"""

from __future__ import annotations

import httpx
import msgspec

from .models import (
    SongDetail,
    QualityDetail,
    SongPrivilege,
    SongDetailResponse,
    QualityDetailResponse,
)
from .request import NeteaseClient
from ...render.models import Author, Metric
from ...utils.exceptions import ParseException

_artist_avatar_cache: dict[int, str] = {}


class ParsedSong:
    """发送层使用的稳定歌曲信息。"""

    def __init__(
        self,
        detail: SongDetail,
        privilege: SongPrivilege | None,
        quality: QualityDetail | None,
        avatar_url: str | None = None,
    ) -> None:
        self.id = str(detail.id)
        self.title = detail.name.strip() or "未知歌曲"
        self.artists = tuple(artist for artist in detail.ar if artist.name.strip())
        self.album = detail.al.name.strip()
        self.cover_url = detail.al.picUrl or None
        self.duration_ms = max(0, detail.dt)
        self.duration = self.duration_ms / 1000
        self.fee = detail.fee
        self.publish_ms = max(0, detail.publishTime)
        self.mv_id = detail.mv
        self.alia = tuple(alias for alias in detail.alia if alias.strip())
        self.privilege = privilege
        self.quality = quality
        resolved_avatar = avatar_url or self.cover_url
        self.author = Author(
            name=" / ".join(artist.name for artist in self.artists) or "未知歌手",
            avatar_url=resolved_avatar,
        )
        self.metrics = (
            Metric(label="时长", value=self.duration_text),
            *((Metric(label="专辑", value=self.album),) if self.album else ()),
        )

    @property
    def duration_text(self) -> str:
        total = max(0, int(self.duration))
        minutes, seconds = divmod(total, 60)
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def source_url(self) -> str:
        return f"https://music.163.com/song/{self.id}"


async def fetch_song(client: NeteaseClient, song_id: str) -> ParsedSong:
    payload = {"c": f'[{{"id":{song_id}}}]'}
    try:
        raw, _ = await client.eapi("/api/v3/song/detail", payload)
    except ParseException:
        raw = await client.weapi("/api/v3/song/detail", payload)
    try:
        response = msgspec.convert(raw, type=SongDetailResponse)
    except (msgspec.ValidationError, TypeError) as exc:
        raise ParseException("网易云歌曲详情结构异常") from exc
    if response.code != 200 or not response.songs:
        raise ParseException("网易云歌曲详情为空")
    detail = response.songs[0]
    primary_artist_id = detail.ar[0].id if detail.ar else 0
    avatar_url = await fetch_artist_avatar(client, primary_artist_id)
    quality = await fetch_quality_detail(client, song_id)
    privilege = response.privileges[0] if response.privileges else None
    return ParsedSong(detail, privilege, quality, avatar_url=avatar_url)


async def fetch_artist_avatar(client: NeteaseClient, artist_id: int) -> str | None:
    if artist_id <= 0:
        return None
    if artist_id in _artist_avatar_cache:
        return _artist_avatar_cache[artist_id]
    payload = {"id": artist_id}
    raw: dict[str, object] | None = None
    try:
        raw, _ = await client.eapi("/api/artist/head/info/get", payload)
    except ParseException:
        try:
            raw = await client.weapi("/api/artist/head/info/get", payload)
        except (ParseException, httpx.HTTPError):
            raw = None
    if raw is None:
        return None
    data = raw.get("data")
    if not isinstance(data, dict):
        return None
    artist = data.get("artist")
    if not isinstance(artist, dict):
        return None
    avatar = artist.get("avatar") or artist.get("cover")
    if isinstance(avatar, str) and avatar:
        if len(_artist_avatar_cache) > 200:
            _artist_avatar_cache.clear()
        _artist_avatar_cache[artist_id] = avatar
        return avatar
    return None


async def fetch_quality_detail(client: NeteaseClient, song_id: str) -> QualityDetail | None:
    if not client.logged_in:
        return None
    payload = {"songId": song_id}
    raw: dict[str, object] | None = None
    try:
        raw, _ = await client.eapi("/api/song/music/detail/get", payload)
    except ParseException:
        try:
            raw = await client.weapi("/api/song/music/detail/get", payload)
        except ParseException:
            raw = None
    if raw is None:
        return None
    try:
        response = msgspec.convert(raw, type=QualityDetailResponse)
    except (msgspec.ValidationError, TypeError):
        return None
    return response.data if response.code == 200 else None
