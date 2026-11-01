"""网易云音源档位选择、试听识别与地址验证。"""

from __future__ import annotations

import msgspec

from .types import QualityLevel, ResolvedAudio, levels_from, normalize_level
from .models import SongUrlItem, SongUrlResponse
from .request import NeteaseClient
from ...utils.exceptions import ParseException

AUDIO_EXTENSIONS = frozenset({"flac", "wav", "m4a", "aac", "mp3", "ogg"})


async def resolve_audio(
    client: NeteaseClient,
    song_id: str,
    preferred: QualityLevel,
) -> ResolvedAudio:
    last_reason = ""
    trial: ResolvedAudio | None = None
    for level in levels_from(preferred):
        for source in ("download", "player"):
            try:
                item = await _request_url(client, song_id, level, source)
            except ParseException as exc:
                last_reason = str(exc)
                continue
            if item is None:
                continue
            resolved = _validate_item(item, level, source)
            if resolved is not None:
                return resolved
            trial_item = _trial_item(item, source)
            if trial_item is not None and trial is None:
                trial = trial_item
            last_reason = "返回的音源不是目标完整音质"
    if trial is not None:
        return trial
    raise ParseException(last_reason or "当前账号没有可用的网易云音源")


async def _request_url(
    client: NeteaseClient,
    song_id: str,
    level: QualityLevel,
    source: str,
) -> SongUrlItem | None:
    if source == "download":
        raw, _ = await client.eapi(
            "/api/song/enhance/download/url/v1",
            _download_payload(song_id, level),
        )
    else:
        raw = await client.xeapi(
            "/api/song/enhance/player/url/v1",
            _player_payload(song_id, level),
        )
    try:
        response = msgspec.convert(raw, type=SongUrlResponse)
    except (msgspec.ValidationError, TypeError) as exc:
        raise ParseException("网易云音源响应结构异常") from exc
    if response.code != 200:
        raise ParseException(f"网易云音源接口返回 {response.code}")
    if response.data is None:
        return None
    if isinstance(response.data, SongUrlItem):
        return response.data
    return response.data[0] if response.data else None


def _download_payload(song_id: str, level: QualityLevel) -> dict[str, object]:
    data: dict[str, object] = {"id": song_id, "level": level}
    if level == "sky":
        data["immerseType"] = "c51"
    if level == "vivid":
        data["encodeType"] = "mp3"
    return data


def _player_payload(song_id: str, level: QualityLevel) -> dict[str, object]:
    data: dict[str, object] = {
        "ids": f"[{song_id}]",
        "level": level,
        "encodeType": "mp3" if level == "vivid" else "flac",
    }
    if level == "sky":
        data["immerseType"] = "c51"
    return data


def _validate_item(item: SongUrlItem, requested: QualityLevel, source: str) -> ResolvedAudio | None:
    if item.code != 200 or not item.url or not item.url.startswith(("http://", "https://")):
        return None
    if item.level is None:
        return None
    actual = normalize_level(item.level)
    if actual is None:
        return None
    if actual != requested:
        return None
    trial_seconds = 0
    if item.freeTrialInfo is not None:
        trial_seconds = max(0, item.freeTrialInfo.end - item.freeTrialInfo.start)
        if trial_seconds <= 0:
            trial_seconds = max(0, item.freeTrialInfo.end)
        if trial_seconds > 0:
            return None
    extension = item.type.lower() if item.type else ""
    if extension not in AUDIO_EXTENSIONS:
        extension = "flac" if actual in ("lossless", "hires", "jymaster", "jyeffect") else "mp3"
    return ResolvedAudio(
        url=item.url,
        level=actual,
        br=max(0, item.br),
        extension=extension,
        source="download" if source == "download" else "player",
        trial_seconds=trial_seconds,
    )


def _trial_item(item: SongUrlItem, source: str) -> ResolvedAudio | None:
    """仅在没有任何完整音源时返回试听，调用方需在卡片上标明。"""
    if item.code != 200 or not item.url or not item.url.startswith(("http://", "https://")):
        return None
    if item.level is None or item.freeTrialInfo is None:
        return None
    actual = normalize_level(item.level)
    if actual is None:
        return None
    seconds = max(0, item.freeTrialInfo.end - item.freeTrialInfo.start)
    if seconds <= 0:
        seconds = max(0, item.freeTrialInfo.end)
    if seconds <= 0:
        return None
    extension = item.type.lower() if item.type else ""
    if extension not in AUDIO_EXTENSIONS:
        extension = "mp3"
    return ResolvedAudio(
        url=item.url,
        level=actual,
        br=max(0, item.br),
        extension=extension,
        source="download" if source == "download" else "player",
        trial_seconds=seconds,
    )
