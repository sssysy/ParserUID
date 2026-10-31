"""酷狗音乐普通版/概念版链接解析器。"""

from __future__ import annotations

import re
from typing import ClassVar
from pathlib import Path
from collections.abc import Sequence

from .vip import ensure_concept_vip
from .auth import KugouCredential, load_credential, credential_from_cookie
from ..base import BaseParser, handle
from .lyric import fetch_lyric
from .crypto import LITE_PROFILE, STANDARD_PROFILE, ApiProfile
from .detail import resolve_identity, resolve_share_hash, identity_from_query
from .models import SongIdentity, QualityOption, PrivilegeResult
from ..config import ParserItemConfig
from .quality import resolve_audio, fetch_privilege
from .request import KugouRequest
from ...sender import ContentItem
from ...utils.models import Platform
from ...utils.exceptions import ParseException

QUALITY_LABELS = {
    "128": "标准 128K",
    "320": "高品质 320K",
    "flac": "无损 FLAC",
    "high": "无损 Hi-Res",
    "super": "DSD",
    "viper_clear": "蝰蛇超清",
    "viper_tape": "蝰蛇母带",
    "viper_atmos": "蝰蛇全景声",
}


class KugouParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="kugou", display_name="酷狗音乐")

    def __init__(self, config: ParserItemConfig | None = None) -> None:
        super().__init__(config)

    @handle(
        "t1.kugou.com",
        r"https?://t1\.kugou\.com/(?P<share_code>[A-Za-z0-9]+)",
    )
    async def _parse_short_link(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        code = searched.group("share_code")
        client = await self.get_client()
        identity = await resolve_share_hash(client, searched.group(0), chain=code)
        return await self._parse_identity(identity)

    @handle(
        "kugou.com",
        r"https?://(?:[A-Za-z0-9-]+\.)*kugou\.com/"
        r"(?:(?:share|mixsong)/[A-Za-z0-9]+\.html(?:\?[^\s#]*)?"
        r"|[^\s#]*[?&](?:hash|chain|album_audio_id)=[^\s#&]+)",
    )
    async def _parse_share(self, searched: re.Match[str]) -> Sequence[ContentItem]:
        url = searched.group(0)
        identity = identity_from_query(url)
        if identity.hash:
            return await self._parse_identity(identity)
        chain_match = re.search(r"[?&]chain=([A-Za-z0-9]+)", url)
        chain = chain_match.group(1) if chain_match is not None else ""
        client = await self.get_client()
        identity = await resolve_share_hash(client, url, chain=chain)
        return await self._parse_identity(identity)

    async def _parse_identity(self, identity: SongIdentity) -> Sequence[ContentItem]:
        if not identity.hash:
            raise ParseException("未找到酷狗歌曲 hash")
        profile = self._active_profile()
        credential = await self._credential_for(profile)
        request = KugouRequest(profile=profile, credential=credential)
        client = await self.get_client()
        meta, merged = await resolve_identity(client, request, identity)

        if profile.is_lite and self._vip_attempt_enabled():
            await ensure_concept_vip(client, request)

        privilege = await fetch_privilege(client, request, merged)
        option = privilege.best_free_quality
        if option is None:
            option = self._select_fallback(privilege)
        if option is None:
            raise ParseException("酷狗没有可用音质（可能需要会员或该歌曲已下架）")
        audio = await resolve_audio(client, request, merged, option)
        lyric = await fetch_lyric(client, merged.hash)

        quality_label = QUALITY_LABELS.get(option.quality, option.quality)
        extra_parts = [f"音质: {quality_label}", f"格式: {audio.extension}", f"时长: {_duration(meta.duration)}"]
        if option.free_listen:
            extra_parts.append("概念版免费听")
        if profile.is_lite:
            extra_parts.append("接口: 概念版")
        else:
            extra_parts.append("接口: 普通版")
        source_url = f"https://www.kugou.com/song/#{merged.hash}"
        if merged.album_audio_id:
            source_url = f"https://h5.kugou.com/v2/v-5a15aeb1/index.html?album_audio_id={merged.album_audio_id}"

        await self.send_info(
            title=meta.title,
            author=self.create_author(meta.singer),
            url=source_url,
            cover_url=meta.cover_url or None,
            extra_info=" | ".join(extra_parts),
        )

        async def load_audio() -> Path:
            return await self.downloader.download_file(
                audio.url,
                filename=f"kugou_{merged.hash.lower()}.{audio.extension}",
                headers={"Referer": "https://www.kugou.com/"},
                proxy=self.proxy,
                fallback_urls=audio.backup_urls,
            )

        content = self.create_audio_content(loader=load_audio, duration=meta.duration)
        items: list[ContentItem] = [content]
        if lyric:
            items.append(self.create_text_content(lyric))
        return self.result(items)

    def _active_profile(self) -> ApiProfile:
        enabled = self.config.values.get("use_concept_api")
        return LITE_PROFILE if enabled is True else STANDARD_PROFILE

    def _vip_attempt_enabled(self) -> bool:
        enabled = self.config.values.get("try_concept_vip")
        return enabled is True

    async def _credential_for(self, profile: ApiProfile) -> KugouCredential:
        key = "concept_cookie" if profile.is_lite else "standard_cookie"
        raw = self.get_config_value(key)
        if raw.strip():
            return credential_from_cookie(raw, mode="manual")
        stored = load_credential("lite" if profile.is_lite else "standard")
        if stored is not None:
            return stored
        raise ParseException(f"未配置酷狗{profile.display_name} Cookie")

    def _select_fallback(self, privilege: object) -> QualityOption | None:
        if not isinstance(privilege, PrivilegeResult):
            return None
        for item in privilege.qualities:
            if item.quality == "128" and item.exists:
                return item
        return None


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"
