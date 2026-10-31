"""网易云歌曲详情与音质文件信息转换。"""

from __future__ import annotations

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


class ParsedSong:
    """发送层使用的稳定歌曲信息。"""

    def __init__(
        self,
        detail: SongDetail,
        privilege: SongPrivilege | None,
        quality: QualityDetail | None,
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
        self.author = Author(name=" / ".join(artist.name for artist in self.artists) or "未知歌手")
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
    raw = await client.weapi("/api/v3/song/detail", {"c": f'[{{"id":{song_id}}}]'})
    try:
        response = msgspec.convert(raw, type=SongDetailResponse)
    except (msgspec.ValidationError, TypeError) as exc:
        raise ParseException("网易云歌曲详情结构异常") from exc
    if response.code != 200 or not response.songs:
        raise ParseException("网易云歌曲详情为空")
    detail = response.songs[0]
    quality = await fetch_quality_detail(client, song_id)
    privilege = response.privileges[0] if response.privileges else None
    return ParsedSong(detail, privilege, quality)


async def fetch_quality_detail(client: NeteaseClient, song_id: str) -> QualityDetail | None:
    if not client.logged_in:
        return None
    try:
        raw = await client.weapi("/api/song/music/detail/get", {"songId": song_id})
    except ParseException:
        return None
    try:
        response = msgspec.convert(raw, type=QualityDetailResponse)
    except (msgspec.ValidationError, TypeError):
        return None
    return response.data if response.code == 200 else None
