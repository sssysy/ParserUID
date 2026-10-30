"""YouTube 数据模型定义。"""

from __future__ import annotations

import msgspec


class VideoData(msgspec.Struct):
    title: str = "YouTube 视频"
    channel: str = ""
    uploader: str = ""
    channel_id: str = ""
    duration: float = 0.0
    timestamp: int | None = None
    thumbnail: str | None = None
    description: str | None = None


def decode_video(data: dict[str, object]) -> VideoData:
    raw_entries = data.get("entries")
    if data.get("_type") == "playlist" and isinstance(raw_entries, list):
        usable: list[dict[str, object]] = []
        for item in raw_entries:
            if isinstance(item, dict):
                usable.append({str(key): value for key, value in item.items()})
        if len(usable) != 1:
            raise ValueError("YouTube 链接返回了多个视频，无法自动选择")
        target_dict = usable[0]
    else:
        target_dict = data

    title = str(target_dict.get("title") or "YouTube 视频")
    if not title.strip():
        title = "YouTube 视频"
    channel = str(target_dict.get("channel") or target_dict.get("uploader") or "")
    uploader = str(target_dict.get("uploader_id") or target_dict.get("uploader") or "")
    channel_id = str(target_dict.get("channel_id") or "")
    thumbnail_raw = target_dict.get("thumbnail")
    thumbnail = str(thumbnail_raw).strip() if thumbnail_raw is not None else None
    desc_raw = target_dict.get("description")
    description = str(desc_raw).strip() if desc_raw is not None else None

    timestamp_raw = target_dict.get("timestamp")
    timestamp = (
        int(timestamp_raw) if isinstance(timestamp_raw, (int, float)) and not isinstance(timestamp_raw, bool) else None
    )

    duration_raw = target_dict.get("duration")
    duration = float(duration_raw) if isinstance(duration_raw, (int, float)) else 0.0

    return VideoData(
        title=title,
        channel=channel,
        uploader=uploader,
        channel_id=channel_id,
        duration=max(duration, 0.0),
        timestamp=timestamp,
        thumbnail=thumbnail,
        description=description,
    )
