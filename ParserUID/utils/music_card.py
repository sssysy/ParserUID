"""QQ 音乐卡片签名客户端。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import msgspec
from msgspec import Struct, field

from gsuid_core.logger import logger

from ..config import proxy_url
from .exceptions import MusicCardException

MUSIC_CARD_ENDPOINT = "https://itapi.top/api/QQMusicJSONArk"
_MAX_RESPONSE_BYTES = 1024 * 1024
_REQUEST_TIMEOUT = 20.0


@dataclass(frozen=True, slots=True)
class MusicCardInfo:
    """签名 QQ 音乐卡片所需的稳定歌曲信息。"""

    audio_url: str
    source_url: str
    title: str
    artist: str
    cover_url: str = ""


class _CardConfig(Struct, frozen=True):
    token: str = ""


class _MusicMeta(Struct, frozen=True):
    musicUrl: str = ""
    jumpUrl: str = ""
    preview: str = ""
    title: str = ""
    desc: str = ""


class _CardMeta(Struct, frozen=True):
    music: _MusicMeta = field(default_factory=_MusicMeta)


class _SignedCard(Struct, frozen=True):
    app: str = ""
    view: str = ""
    config: _CardConfig = field(default_factory=_CardConfig)
    meta: _CardMeta = field(default_factory=_CardMeta)


class _CardResponse(Struct, frozen=True):
    code: int = 0
    message: str = ""
    data: _SignedCard | None = None


async def sign_music_card(info: MusicCardInfo) -> str:
    """请求签名服务并返回原样可发送的 QQ 音乐卡片 JSON。"""
    _validate_info(info)
    params = {"url": info.audio_url, "jump": info.source_url}
    if info.title:
        params["song"] = info.title
    if info.artist:
        params["singer"] = info.artist
    if info.cover_url:
        params["cover"] = info.cover_url

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=10.0),
            follow_redirects=False,
            proxy=proxy_url(),
        ) as client:
            response = await client.get(MUSIC_CARD_ENDPOINT, params=params)
    except (httpx.HTTPError, OSError, TimeoutError) as exc:
        logger.warning(f"[Parser·音乐卡片] 签名请求失败 | error_type={type(exc).__name__}")
        raise MusicCardException("QQ 音乐卡片签名服务不可用") from exc

    if response.status_code != 200:
        logger.warning(f"[Parser·音乐卡片] 签名服务状态异常 | status={response.status_code}")
        raise MusicCardException("QQ 音乐卡片签名服务返回错误")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        logger.warning("[Parser·音乐卡片] 签名响应超过大小限制")
        raise MusicCardException("QQ 音乐卡片签名响应过大")

    try:
        payload = msgspec.json.decode(response.content, type=_CardResponse)
    except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
        logger.warning(f"[Parser·音乐卡片] 签名响应解析失败 | error_type={type(exc).__name__}")
        raise MusicCardException("QQ 音乐卡片签名响应无效") from exc

    if payload.code not in {0, 1} or not payload.message:
        logger.warning(f"[Parser·音乐卡片] 签名服务拒绝请求 | code={payload.code}")
        raise MusicCardException("QQ 音乐卡片签名失败")

    try:
        card = msgspec.json.decode(payload.message, type=_SignedCard)
    except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
        logger.warning(f"[Parser·音乐卡片] 签名卡片解析失败 | error_type={type(exc).__name__}")
        raise MusicCardException("QQ 音乐卡片内容无效") from exc

    _validate_card(card, info)
    return payload.message


def _validate_info(info: MusicCardInfo) -> None:
    if not info.title.strip():
        raise MusicCardException("QQ 音乐卡片缺少歌曲标题")
    if not _is_http_url(info.audio_url) or not _is_http_url(info.source_url):
        raise MusicCardException("QQ 音乐卡片链接无效")
    if info.cover_url and not _is_http_url(info.cover_url):
        raise MusicCardException("QQ 音乐卡片封面链接无效")


def _validate_card(card: _SignedCard, info: MusicCardInfo) -> None:
    if card.app != "com.tencent.music.lua" or card.view != "music":
        raise MusicCardException("QQ 音乐卡片类型无效")
    if not card.config.token:
        raise MusicCardException("QQ 音乐卡片缺少签名")
    music = card.meta.music
    if music.musicUrl != info.audio_url and music.musicUrl.replace(" ", "+") != info.audio_url:
        raise MusicCardException("QQ 音乐卡片播放地址不一致")
    if music.jumpUrl != info.source_url:
        raise MusicCardException("QQ 音乐卡片跳转地址不一致")


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )
