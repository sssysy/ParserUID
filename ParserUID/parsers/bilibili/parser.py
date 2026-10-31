from __future__ import annotations

import re
import asyncio
from typing import TYPE_CHECKING, ClassVar, TypedDict
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
from collections.abc import Mapping

import msgspec
from aiohttp import ClientError

from gsuid_core.logger import logger
from gsuid_core.data_store import get_res_path

from .auth import (
    QrLogin,
    PollResult,
    BiliAuthError,
    BiliCredential,
    poll_login,
    load_credential,
    save_credential,
    refresh_credential,
)
from ..base import BaseParser, handle
from .utils import (
    av2bv,
    bv2av,
    metric,
    clean_cover,
    normalize_url,
    extract_rich_text,
    parse_dynamic_time,
    format_dynamic_time,
    is_allowed_media_host,
    is_bilibili_media_host,
)
from .models import (
    BiliUserCard,
    BiliVideoInfo,
    BiliDynamicAuthor,
    BiliDynamicContent,
    decode_live,
    decode_user,
    decode_video,
    decode_bangumi,
    decode_favorite,
)
from .comment import comment_limit, decode_comments as decode_card_comments
from ...sender import ContentItem
from ...utils.ffmpeg import merge_av
from ...utils.models import Platform, MediaContent
from ...render.models import Author, Metric, Comment, PreviewCard
from ...utils.helpers import build_card_blocks, build_image_blocks
from ...utils.exceptions import ParseException

if TYPE_CHECKING:
    from ..config import ParserItemConfig

QUALITY_MAP: ClassVar[dict[str, int]] = {
    "360p": 16,
    "480p": 32,
    "720p": 64,
    "1080p": 80,
    "1080p+": 112,
    "1080p60": 116,
    "4k": 120,
    "8k": 127,
}

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


class StreamSelection(TypedDict):
    video: list[str]
    audio: list[list[str]]
    quality: str
    resolution: str
    duration: int


class StreamCandidate(TypedDict):
    urls: list[str]
    quality_id: int
    quality: str
    resolution: str
    codec: str


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


class BilibiliParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="bilibili", display_name="哔哩哔哩")
    API_VIEW: ClassVar[str] = "https://api.bilibili.com/x/web-interface/wbi/view"
    API_VIEW_FALLBACK: ClassVar[str] = "https://api.bilibili.com/x/web-interface/view"
    API_PLAYURL: ClassVar[str] = "https://api.bilibili.com/x/player/wbi/playurl"
    API_PLAYURL_FALLBACK: ClassVar[str] = "https://api.bilibili.com/x/player/playurl"
    API_FAVORITE: ClassVar[str] = "https://api.bilibili.com/x/v3/fav/resource/list"
    API_BANGUMI: ClassVar[str] = "https://api.bilibili.com/pgc/view/web/season"
    API_LIVE: ClassVar[str] = "https://api.live.bilibili.com/room/v1/Room/get_info"
    API_USER: ClassVar[str] = "https://api.bilibili.com/x/web-interface/card"
    API_COMMENTS: ClassVar[str] = "https://api.bilibili.com/x/v2/reply/main"
    API_COMMENTS_FALLBACK: ClassVar[str] = "https://api.bilibili.com/x/v2/reply"
    API_DYNAMIC: ClassVar[str] = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"
    API_OPUS: ClassVar[str] = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail"
    ARTICLE_CARD_API: ClassVar[str] = "https://api.bilibili.com/x/article/view"
    DANMAKU_API: ClassVar[str] = "https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
    RETRYABLE_STATUSES: ClassVar[frozenset[int]] = frozenset({403, 404, 408, 425, 429, *range(500, 600)})
    BLOCKED_PCDN_DOMAINS: ClassVar[tuple[str, ...]] = (
        "mcdn.bilivideo.com",
        "mcdn.bilivideo.cn",
        "szbdyd.com",
        "mountaintoys.cn",
        "nexusedgeio.com",
        "ahdohpiechei.com",
    )

    def __init__(self, config: ParserItemConfig | None = None) -> None:
        super().__init__(config)
        self.headers.update(
            {
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        self._credential_file = get_res_path("ParserUID") / "cookies" / "bilibili_credential.json"
        self._credential: BiliCredential | None = load_credential(self._credential_file)
        self._set_cookies()
        self._credential_lock = asyncio.Lock()
        self._login_lock = asyncio.Lock()
        self._user_cache: dict[int | str, BiliUserCard | None] = {}
        self._qr_login: QrLogin | None = None

    @property
    def media_headers(self) -> dict[str, str]:
        headers = self.headers.copy()
        headers["Referer"] = "https://www.bilibili.com/"
        return headers

    @property
    def quality(self) -> int:
        value = self.get_config_value("video_quality", "auto").strip().lower()
        return QUALITY_MAP.get(value, 127)

    def _set_cookies(self) -> None:
        cookies_str = self.get_config_value("cookies")
        if self._credential is not None:
            cookies_str = self._credential.cookie_header
        if cookies_str:
            self.headers["Cookie"] = cookies_str

    def _request_headers(self, url: str | None = None) -> dict[str, str]:
        del url
        self._set_cookies()
        return self.headers.copy()

    async def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
        error: str,
    ) -> dict[str, object]:
        retries = 2
        last_error: Exception | None = None
        await self._ensure_credential()
        client = await self.get_client()
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    url,
                    params=params,
                    headers=headers or self._request_headers(url),
                )
                if response.status_code >= 400:
                    if response.status_code == 412:
                        last_error = ClientError("HTTP 412")
                        break
                    raise ClientError(f"HTTP {response.status_code}")
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("响应不是 JSON 对象")
                return dict(payload)
            except (ClientError, TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(1 + attempt)
        logger.warning(
            f"[哔哩哔哩] 请求失败 | host={urlparse(url).hostname or 'unknown'} | error_type={type(last_error).__name__}"
        )
        raise ParseException(error) from last_error

    async def _get_bytes(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
        error: str,
    ) -> bytes:
        retries = 2
        last_error: Exception | None = None
        client = await self.get_client()
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    url,
                    params=params,
                    headers=headers or self._request_headers(url),
                )
                if response.status_code >= 400:
                    raise ClientError(f"HTTP {response.status_code}")
                return response.content
            except (ClientError, TimeoutError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(1 + attempt)
        logger.warning(
            f"[哔哩哔哩] 请求失败 | host={urlparse(url).hostname or 'unknown'} | error_type={type(last_error).__name__}"
        )
        raise ParseException(error) from last_error

    @handle("b23.tv", r"b23\.tv/[0-9A-Za-z._?%&+\-=/#]+")
    @handle("bili2233", r"bili2233\.cn/[0-9A-Za-z._?%&+\-=/#]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = normalize_url(searched.group(0))
        if not url.startswith("http"):
            url = f"https://{url}"
        final_url = await self.get_final_url(url, headers=self._request_headers(url))
        for keyword, (pattern, handler) in self._handlers.items():
            if keyword not in final_url:
                continue
            matched = pattern.search(final_url)
            if matched is not None:
                return await handler(matched)
        raise ParseException("短链跳转后未匹配到哔哩哔哩内容")

    @handle(
        "/BV",
        r"bilibili\.com(?:/video)?/(?P<bvid>BV[0-9A-Za-z]{10})"
        r"(?:[?&]p=(?P<page>\d{1,3}))?",
    )
    @handle(
        "BV",
        r"^(?P<bvid>BV[0-9A-Za-z]{10})(?:\s+(?P<page>\d{1,3}))?$",
    )
    async def _parse_bv(self, searched: re.Match[str]):
        bvid = searched.group("bvid")
        page = int(searched.groupdict().get("page") or 1)
        return await self._parse_video(bvid=bvid, page=page)

    @handle(
        "/av",
        r"bilibili\.com(?:/video)?/av(?P<avid>\d{5,})"
        r"(?:[?&]p=(?P<page>\d{1,3}))?",
    )
    @handle(
        "av",
        r"^av(?P<avid>\d{5,})(?:\s+(?P<page>\d{1,3}))?$",
    )
    async def _parse_av(self, searched: re.Match[str]):
        avid = int(searched.group("avid"))
        page = int(searched.groupdict().get("page") or 1)
        return await self._parse_video(bvid=av2bv(avid), avid=avid, page=page)

    @handle("/dynamic/", r"bilibili\.com/dynamic/(?P<dynamic_id>\d+)")
    @handle("t.bili", r"t\.bilibili\.com/(?P<dynamic_id>\d+)")
    async def _parse_dynamic(self, searched: re.Match[str]):
        return await self._parse_dynamic_by_id(searched.group("dynamic_id"))

    @handle("/opus/", r"bilibili\.com/opus/(?P<opus_id>\d+)")
    async def _parse_opus(self, searched: re.Match[str]):
        return await self._parse_opus_by_id(searched.group("opus_id"))

    @handle("/read/", r"bilibili\.com/read/cv(?P<read_id>\d+)")
    async def _parse_read(self, searched: re.Match[str]):
        read_id = int(searched.group("read_id"))
        try:
            return await self._parse_opus_by_id(str(read_id))
        except ParseException:
            return await self._parse_article_by_id(read_id)

    @handle("live.bili", r"live\.bilibili\.com/(?P<room_id>\d+)")
    async def _parse_live(self, searched: re.Match[str]):
        return await self._parse_live_by_id(int(searched.group("room_id")))

    @handle("/favlist", r"favlist\?fid=(?P<fav_id>\d+)")
    async def _parse_favorite(self, searched: re.Match[str]):
        return await self._parse_favorite_by_id(int(searched.group("fav_id")))

    @handle(
        "bilibili.com/bangumi/play",
        r"bilibili\.com/bangumi/play/(?P<kind>ep|ss)(?P<id>\d+)",
    )
    async def _parse_bangumi(self, searched: re.Match[str]):
        return await self._parse_bangumi_by_id(searched.group("kind"), int(searched.group("id")))

    async def _fetch_video(self, bvid: str) -> BiliVideoInfo:
        try:
            payload = await self._get_json(
                self.API_VIEW,
                params={"bvid": bvid},
                error="获取视频信息失败",
            )
        except ParseException:
            payload = await self._get_json(
                self.API_VIEW_FALLBACK,
                params={"bvid": bvid},
                error="获取视频信息失败",
            )
        try:
            info = decode_video(payload)
        except (msgspec.ValidationError, ValueError) as exc:
            raise ParseException("视频信息解析失败") from exc
        if not info.bvid:
            raise ParseException("视频不存在或不可访问")
        return info

    async def _fetch_user(self, mid: int | str) -> BiliUserCard | None:
        if not mid:
            return None
        if mid in self._user_cache:
            return self._user_cache[mid]
        try:
            payload = await self._get_json(
                self.API_USER,
                params={"mid": mid},
                error="获取 UP 主资料失败",
            )
        except ParseException:
            return None
        user = decode_user(payload)
        self._user_cache[mid] = user
        return user

    async def _parse_video(
        self,
        *,
        bvid: str,
        page: int = 1,
        avid: int | None = None,
    ):
        info = await self._fetch_video(bvid)
        pages = info.pages or []
        page_index = page - 1
        if not pages:
            pages_duration = info.duration
            cid = info.cid
            page_title = info.title
            cover = info.cover_url
        else:
            page_index %= len(pages)
            selected = pages[page_index]
            pages_duration = selected.duration
            cid = selected.cid
            page_title = info.title
            if len(pages) > 1:
                page_title = f"{info.title} | 分集 - {selected.part}"
            cover = clean_cover(selected.first_frame) or info.cover_url
        if not cid:
            raise ParseException("视频分集信息缺失")

        await self._ensure_credential()
        stream = await self._fetch_stream(
            bvid=info.bvid,
            aid=info.aid or 0,
            cid=cid,
            duration=pages_duration,
        )
        stream_urls = stream["video"]
        audio_urls = stream["audio"]
        quality_label = stream["quality"]
        resolution = stream["resolution"]
        user = await self._fetch_user(info.owner.mid)
        author = self._build_author(info.owner.name, info.owner.face, info.owner.mid, user)
        url = f"https://www.bilibili.com/video/{info.bvid}"
        if page_index > 0:
            url += f"?p={page_index + 1}"
        metrics = info.stat.metrics
        extra_info: str | None = None
        if getattr(self.mycfg, "danmaku", True):
            danmaku = await self._fetch_hot_danmaku(cid)
            if danmaku:
                extra_info = "热门弹幕 · " + "  ".join(danmaku)
        comments = await self._fetch_comments(info.aid)
        await self.send_info(
            card_type="video",
            title=page_title,
            author=author,
            cover_url=cover,
            text=info.desc,
            url=url,
            extra_info=extra_info,
            metrics=metrics,
            comments=comments,
            video_duration=f"{pages_duration}s" if pages_duration > 0 else None,
            video_quality=quality_label if not resolution else f"{quality_label} · {resolution}",
        )
        if not stream_urls:
            raise ParseException("未找到可用的视频流")
        content = self._build_video_content(
            stream_urls,
            audio_urls,
            cover,
            pages_duration,
        )
        return self.result([content])

    async def _fetch_comments(self, aid: int) -> list[Comment]:
        if not bool(getattr(self.mycfg, "include_comments", False)):
            return []
        limit = comment_limit(getattr(self.mycfg, "comment_limit", 0))
        if limit <= 0 or aid <= 0:
            return []

        payload = await self._get_comment_payload(
            self.API_COMMENTS,
            params={"type": 1, "oid": aid, "mode": 3, "next": 0},
            error="获取哔哩哔哩热评失败",
        )
        if payload is None:
            payload = await self._get_comment_payload(
                self.API_COMMENTS_FALLBACK,
                params={"type": 1, "oid": aid, "pn": 1, "sort": 2, "ps": limit},
                error="获取哔哩哔哩评论失败",
            )
        if payload is None:
            return []
        try:
            return decode_card_comments(
                payload,
                limit=limit,
            )
        except (msgspec.ValidationError, ValueError):
            logger.warning(f"[哔哩哔哩] 评论数据异常 | aid={aid} | error_type=validation")
            return []

    async def _get_comment_payload(
        self,
        url: str,
        *,
        params: dict[str, str | int],
        error: str,
    ) -> dict[str, object] | None:
        try:
            payload = await self._get_json(
                url,
                params=params,
                headers=self._request_headers(url),
                error=error,
            )
        except ParseException:
            return None
        if payload.get("code") != 0:
            logger.warning(f"[哔哩哔哩] 评论接口返回错误 | code={payload.get('code')} | endpoint={urlparse(url).path}")
            return None
        data = payload.get("data")
        return payload if isinstance(data, dict) else None

    async def _fetch_stream(
        self,
        *,
        bvid: str,
        aid: int,
        cid: int,
        duration: int,
    ) -> StreamSelection:
        if not cid:
            raise ParseException("视频分集信息缺失")
        params = {
            "bvid": bvid,
            "avid": aid or bv2av(bvid),
            "cid": cid,
            "qn": self.quality,
            "fnver": 0,
            "fnval": 4048,
            "fourk": 1,
        }
        data: dict[str, object] | None = None
        message = "获取视频播放流失败"
        last_candidate: dict[str, object] | None = None
        base_headers = self._request_headers()
        base_headers.pop("Cookie", None)
        for use_auth in (True, False):
            for api_url in (self.API_PLAYURL, self.API_PLAYURL_FALLBACK):
                headers = self._request_headers(api_url) if use_auth else dict(base_headers)
                try:
                    payload = await self._get_json(
                        api_url,
                        params=params,
                        headers=headers,
                        error="获取视频播放流失败",
                    )
                except ParseException:
                    continue
                if payload.get("code") != 0:
                    message = str(payload.get("message") or message)
                    continue
                candidate = payload.get("data")
                if not isinstance(candidate, dict):
                    continue
                if self._has_playable_stream(candidate):
                    data = candidate
                    break
                last_candidate = candidate
            if data is not None:
                break
        if data is None:
            data = last_candidate
        if data is None:
            raise ParseException(message)
        dash = data.get("dash")
        raw_videos = dash.get("video") if isinstance(dash, dict) else None
        raw_audios = dash.get("audio") if isinstance(dash, dict) else None
        videos: list[StreamCandidate] = []
        if isinstance(dash, dict):
            videos = self._normalize_streams(raw_videos, kind="video")
            audios = self._normalize_streams(raw_audios, kind="audio")
            if videos:
                selected = self._select_video_stream(videos)
                return StreamSelection(
                    video=_string_list(selected["urls"]),
                    audio=[_string_list(item["urls"]) for item in audios],
                    quality=str(selected["quality"]),
                    resolution=str(selected["resolution"]),
                    duration=duration,
                )
        durl = data.get("durl")
        if isinstance(durl, list):
            urls = self._normalize_durl(durl)
            if urls:
                return StreamSelection(
                    video=urls,
                    audio=[],
                    quality="MP4",
                    resolution="",
                    duration=duration,
                )
        logger.warning(
            "[哔哩哔哩] 视频流不可用 | "
            f"bvid={bvid} | cid={cid} | qn={self.quality} | "
            f"dash_video={len(raw_videos) if isinstance(raw_videos, list) else 0} | "
            f"allowed_video={len(videos) if isinstance(dash, dict) else 0} | "
            f"durl={len(durl) if isinstance(durl, list) else 0}"
        )
        raise ParseException("未找到可用的视频流")

    @staticmethod
    def _has_playable_stream(data: dict[str, object]) -> bool:
        dash = data.get("dash")
        if isinstance(dash, dict) and dash.get("video"):
            return True
        durl = data.get("durl")
        return isinstance(durl, list) and bool(durl)

    def _normalize_durl(self, durl: list[object]) -> list[str]:
        candidates: list[str] = []
        for item in durl:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if isinstance(url, str) and url:
                candidates.append(url)
            backup = item.get("backup_url") or item.get("backupUrl") or []
            if not isinstance(backup, list):
                backup = [backup]
            candidates.extend(u for u in backup if isinstance(u, str) and u)

        allowed = [url for url in candidates if is_allowed_media_host(url)]
        direct = [url for url in allowed if is_bilibili_media_host(url) and not self._is_pcdn_url(url)]
        pcdn = [url for url in allowed if self._is_pcdn_url(url)]
        return direct + pcdn

    def _normalize_streams(self, streams: object, *, kind: str) -> list[StreamCandidate]:
        if not isinstance(streams, list):
            return []
        result: list[StreamCandidate] = []
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            base = stream.get("baseUrl") or stream.get("base_url")
            if not isinstance(base, str) or not base:
                continue
            backup = _string_list(stream.get("backupUrl") or stream.get("backup_url") or [])
            urls = [base, *backup]
            direct_urls = [u for u in urls if is_bilibili_media_host(u) and not self._is_pcdn_url(u)]
            if direct_urls:
                urls = direct_urls
            elif any(is_allowed_media_host(u) for u in urls):
                # B 站偶发把主地址和备份地址全部下发到 PCDN。此时直连地址
                # 不存在，保留原始候选作为兜底，避免整条视频被误判为无流。
                urls = [u for u in urls if is_allowed_media_host(u)]
            else:
                continue
            quality_id = self._stream_int(stream.get("id"))
            width = self._stream_int(stream.get("width"))
            height = self._stream_int(stream.get("height"))
            label = self._quality_label(quality_id) if kind == "video" else "音频"
            result.append(
                StreamCandidate(
                    urls=urls,
                    quality_id=quality_id,
                    quality=label,
                    resolution=f"{width} × {height}" if width and height else "",
                    codec=str(stream.get("codecs") or ""),
                )
            )
        if kind == "video":
            preferred = self._codec_rank()
            result.sort(
                key=lambda item: (
                    item["quality_id"],
                    preferred(item["codec"]),
                ),
                reverse=True,
            )
        else:
            result.sort(key=lambda item: item["quality_id"], reverse=True)
        return result

    def _select_video_stream(self, videos: list[StreamCandidate]) -> StreamCandidate:
        """按配置选择画质；auto 取最高，指定画质优先精确匹配且不超出偏好。"""
        preference = self.get_config_value("video_quality", "auto").strip().lower()
        if preference == "auto" or preference not in QUALITY_MAP:
            return videos[0]

        target = QUALITY_MAP[preference]
        for candidate in videos:
            if candidate["quality_id"] == target:
                return candidate
        within_preference = [candidate for candidate in videos if candidate["quality_id"] <= target]
        return within_preference[0] if within_preference else videos[-1]

    @staticmethod
    def _stream_int(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _codec_rank(self):
        preference = str(getattr(self.mycfg, "video_codec", "auto") or "auto")
        aliases = {
            "auto": ("avc", "hev", "av01"),
            "avc": ("avc",),
            "hev": ("hev",),
            "av1": ("av01",),
        }
        order = list(reversed(aliases.get(preference.lower(), aliases["auto"])))

        def rank(codec: str) -> int:
            lowered = codec.lower()
            return next(
                (index for index, name in enumerate(order) if name in lowered),
                -1,
            )

        return rank

    def _is_pcdn_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == domain or host.endswith(f".{domain}") for domain in self.BLOCKED_PCDN_DOMAINS)

    @staticmethod
    def _quality_label(quality_id: int) -> str:
        return {
            127: "8K",
            126: "杜比视界",
            125: "HDR",
            120: "4K",
            116: "1080P60",
            112: "1080P+",
            80: "1080P",
            74: "720P60",
            64: "720P",
            32: "480P",
            16: "360P",
            6: "240P",
        }.get(quality_id, f"{quality_id}P")

    def _build_video_content(
        self,
        videos: list[str],
        audios: list[list[str]],
        cover: str | None,
        duration: int,
    ) -> MediaContent:
        digest = sha256(videos[0].split("?", 1)[0].encode()).hexdigest()[:16]
        output_name = f"bilibili_{digest}.mp4"

        async def load() -> Path:
            output_path = self.downloader.cache_dir / output_name
            if output_path.exists():
                return output_path
            if audios:
                last_error: ParseException | OSError | None = None
                for audio_urls in audios:
                    for video_url in videos:
                        for audio_url in audio_urls:
                            try:
                                v_tmp = await self.downloader.download_video(
                                    video_url,
                                    headers=self.media_headers,
                                    proxy=self.proxy,
                                )
                                a_tmp = await self.downloader.download_audio(
                                    audio_url,
                                    headers=self.media_headers,
                                    proxy=self.proxy,
                                )
                                return await merge_av(
                                    video_path=v_tmp,
                                    audio_path=a_tmp,
                                    output_path=output_path,
                                )
                            except (ParseException, OSError) as exc:
                                last_error = exc
                if isinstance(last_error, ParseException):
                    raise last_error
                raise ParseException(str(last_error or "视频音轨下载或合并失败")) from last_error
            last_error: ParseException | OSError | None = None
            for video_url in videos:
                try:
                    return await self.downloader.download_video(
                        video_url,
                        filename=output_name,
                        headers=self.media_headers,
                        proxy=self.proxy,
                    )
                except (ParseException, OSError) as exc:
                    last_error = exc
            if isinstance(last_error, ParseException):
                raise last_error
            raise ParseException(str(last_error or "视频流下载失败")) from last_error

        return self.create_video_content(
            None,
            loader=load,
            duration=duration,
            proxy=self.proxy,
        )

    async def close_session(self) -> None:
        self._user_cache.clear()
        self._credential = None
        await super().close_session()

    async def _ensure_credential(self) -> BiliCredential | None:
        if self._credential is not None and not self._credential.needs_refresh:
            return self._credential

        async with self._credential_lock:
            if self._credential is None:
                if self._credential_file.exists():
                    self._credential = load_credential(self._credential_file)
                    if self._credential is not None:
                        self._set_cookies()
                if self._credential is None:
                    return None

            if not self._credential.needs_refresh:
                return self._credential

            if not self._credential.can_refresh:
                logger.warning("[哔哩哔哩] 凭证过期，缺少 access/refresh_token")
                return self._credential
            try:
                refreshed = await refresh_credential(
                    self._credential,
                    proxy=self.proxy,
                )
            except BiliAuthError as exc:
                logger.warning(f"[Parser·哔哩哔哩] 凭证续期失败: {exc}")
                return self._credential
            self._credential = refreshed
            await asyncio.to_thread(save_credential, self._credential_file, refreshed)
            self._set_cookies()
            logger.info("[Parser·哔哩哔哩] 凭证续期成功")
            return self._credential

    async def login_with_qrcode(self) -> bytes:
        self._qr_login = QrLogin(proxy=self.proxy)
        return await self._qr_login.generate_qrcode()

    async def complete_qr_login(self) -> PollResult:
        if self._qr_login is None:
            raise ParseException("请先生成哔哩哔哩登录二维码")
        async with self._login_lock:
            result = await poll_login(self._qr_login)
        if result.credential is not None:
            await self.apply_credential(result.credential)
        return result

    async def apply_credential(self, credential: BiliCredential) -> None:
        """更新内存 Cookie 并把登录凭证原子写入数据目录。"""
        async with self._credential_lock:
            self._credential = credential
            self._set_cookies()
            await asyncio.to_thread(save_credential, self._credential_file, credential)
        logger.info(f"[Parser·哔哩哔哩] 登录凭证已保存 | mid={credential.mid}")

    async def _fetch_hot_danmaku(self, cid: int) -> list[str]:
        """获取高频弹幕；失败只影响卡片附加信息，不阻断解析。"""
        if not cid:
            return []
        try:
            raw = await self._get_bytes(
                self.DANMAKU_API.format(cid=cid),
                error="获取弹幕失败",
            )
        except ParseException:
            return []
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return []
        counts: dict[str, int] = {}
        for node in root.findall(".//d"):
            text = (node.text or "").strip()
            if not text or len(text) > 60:
                continue
            counts[text] = counts.get(text, 0) + 1
        return [text for text, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:5]]

    def _build_author(
        self,
        name: str,
        face: str,
        mid: int | str,
        user: BiliUserCard | None,
    ) -> Author:
        return self.create_author(
            name or (user.name if user else ""),
            clean_cover(face or (user.face if user else "")),
            description=user.sign if user else None,
            uid=str(mid) if mid else None,
            metrics=user.metrics if user else (),
        )

    async def _parse_dynamic_by_id(self, dynamic_id: str):
        payload = await self._get_json(
            self.API_DYNAMIC,
            params={"id": dynamic_id},
            error="获取动态信息失败",
        )
        if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise ParseException(str(payload.get("message") or "动态不存在"))
        item = payload["data"].get("item")
        if not isinstance(item, dict):
            raise ParseException("动态内容为空")
        dynamic = self._build_dynamic(item, dynamic_id)
        return await self._finish_dynamic(dynamic)

    async def _parse_opus_by_id(self, opus_id: str):
        payload = await self._get_json(
            self.API_OPUS,
            params={"id": opus_id, "timezone_offset": -480},
            error="获取图文信息失败",
        )
        if payload.get("code") != 0:
            return await self._parse_dynamic_by_id(opus_id)
        if not isinstance(payload.get("data"), dict):
            raise ParseException(str(payload.get("message") or "图文不存在"))
        item = payload["data"].get("item")
        if not isinstance(item, dict):
            raise ParseException("图文内容为空")
        dynamic = self._build_dynamic(item, opus_id)
        if not dynamic.cover and dynamic.is_article:
            try:
                dyn_payload = await self._get_json(
                    self.API_DYNAMIC,
                    params={"id": opus_id},
                    error="",
                )
                if dyn_payload.get("code") == 0 and isinstance(dyn_payload.get("data"), dict):
                    dyn_item = dyn_payload["data"].get("item")
                    if isinstance(dyn_item, dict):
                        dyn_content = self._build_dynamic(dyn_item, opus_id)
                        if dyn_content.cover:
                            dynamic.cover = dyn_content.cover
                        if not dynamic.images and dyn_content.images:
                            dynamic.images = dyn_content.images
            except ParseException:
                logger.warning(f"[哔哩哔哩] 专栏头图补全失败 | opus_id={opus_id}")
        return await self._finish_dynamic(dynamic)

    @staticmethod
    def _dynamic_modules(modules: object) -> dict[str, dict[str, object]]:
        if isinstance(modules, dict):
            return {key: value for key, value in modules.items() if isinstance(key, str) and isinstance(value, dict)}
        if not isinstance(modules, list):
            return {}
        result: dict[str, dict[str, object]] = {}
        for module in modules:
            if not isinstance(module, dict):
                continue
            module_type = module.get("module_type")
            if not isinstance(module_type, str):
                continue
            for key, value in module.items():
                if key != "module_type" and key.startswith("module_") and isinstance(value, dict):
                    result.setdefault(key, value)
        return result

    @staticmethod
    def _dynamic_rich_text(value: object) -> str:
        if not isinstance(value, dict):
            return ""
        return extract_rich_text(value.get("rich_text_nodes") or value.get("nodes")) or str(value.get("text") or "")

    @staticmethod
    def _extract_paragraph_text(paragraph: dict[str, object]) -> str:
        text_obj = paragraph.get("text")
        if isinstance(text_obj, dict):
            t = extract_rich_text(text_obj.get("rich_text_nodes") or text_obj.get("nodes")) or str(
                text_obj.get("text") or ""
            )
            if t.strip():
                return t.strip()
        elif isinstance(text_obj, str) and text_obj.strip():
            return text_obj.strip()

        list_obj = paragraph.get("list")
        if isinstance(list_obj, dict):
            items = list_obj.get("items")
            if isinstance(items, list):
                lines: list[str] = []
                for it in items:
                    if isinstance(it, dict):
                        line = extract_rich_text(it.get("rich_text_nodes") or it.get("nodes")) or str(
                            it.get("text") or ""
                        )
                        if line.strip():
                            lines.append(f"• {line.strip()}")
                if lines:
                    return "\n".join(lines)

        for key in ("heading", "blockquote", "code"):
            sub = paragraph.get(key)
            if isinstance(sub, dict):
                sub_t = extract_rich_text(sub.get("rich_text_nodes") or sub.get("nodes")) or str(sub.get("text") or "")
                if sub_t.strip():
                    return sub_t.strip()
            elif isinstance(sub, str) and sub.strip():
                return sub.strip()
        return ""

    @staticmethod
    def _dynamic_content(
        modules: dict[str, dict[str, object]],
    ) -> tuple[str, list[str], list[tuple[str, str]]]:
        all_texts: list[str] = []
        images: list[str] = []
        segments: list[tuple[str, str]] = []
        pending_texts: list[str] = []

        def flush_pending() -> None:
            if pending_texts:
                combined = "\n".join(pending_texts).strip()
                pending_texts.clear()
                if combined:
                    segments.append(("text", combined))

        content = modules.get("module_content")
        if content is not None:
            paragraphs = content.get("paragraphs")
            if isinstance(paragraphs, list):
                for paragraph in paragraphs:
                    if not isinstance(paragraph, dict):
                        continue
                    text = BilibiliParser._extract_paragraph_text(paragraph)
                    if text:
                        all_texts.append(text)
                        pending_texts.append(text)
                    pic = paragraph.get("pic")
                    if isinstance(pic, dict):
                        flush_pending()
                        for item in pic.get("pics") or []:
                            if not isinstance(item, dict):
                                continue
                            url = item.get("url")
                            if isinstance(url, str) and is_allowed_media_host(url):
                                normalized = normalize_url(url)
                                if normalized not in images:
                                    images.append(normalized)
                                segments.append(("image", normalized))
                flush_pending()

        return "\n".join(all_texts), images, segments

    def _build_dynamic(self, item: dict[str, object], ident: str) -> BiliDynamicContent:
        modules = self._dynamic_modules(item.get("modules"))
        author_data = modules.get("module_author")
        author = BiliDynamicAuthor()
        if isinstance(author_data, dict):
            author = BiliDynamicAuthor(
                mid=str(author_data.get("mid") or ""),
                name=str(author_data.get("name") or ""),
                face=normalize_url(str(author_data.get("face") or "")),
                sign=str(author_data.get("sign") or ""),
                timestamp=parse_dynamic_time(author_data.get("pub_ts") or author_data.get("pub_time")),
            )
        basic = item.get("basic")
        basic_title = str(basic.get("title") or "") if isinstance(basic, dict) else ""
        title = "" if basic_title.endswith("的动态 - 哔哩哔哩") else basic_title
        article_type = str(basic.get("article_type") or "0") if isinstance(basic, dict) else "0"
        is_article = article_type.isdigit() and int(article_type) > 0
        title_data = modules.get("module_title")
        if isinstance(title_data, dict):
            title = title or str(title_data.get("text") or "")
        dynamic_data = modules.get("module_dynamic")
        text, images, segments = self._dynamic_content(modules)
        cover: str | None = None
        url = f"https://t.bilibili.com/{ident}"
        top_data = modules.get("module_top")
        if isinstance(top_data, dict):
            album = top_data.get("album")
            display = top_data.get("display")
            if not isinstance(album, dict) and isinstance(display, dict):
                album = display.get("album")
            if isinstance(album, dict):
                for pic in album.get("pics") or []:
                    if isinstance(pic, dict) and isinstance(pic.get("url"), str):
                        source = pic["url"]
                        if is_allowed_media_host(source):
                            normalized = normalize_url(source)
                            if normalized not in images:
                                images.append(normalized)
        if isinstance(dynamic_data, dict):
            desc = dynamic_data.get("desc")
            if isinstance(desc, dict):
                desc_text = self._dynamic_rich_text(desc)
                text = text or desc_text
                if desc_text and not segments:
                    segments.append(("text", desc_text))
            major = dynamic_data.get("major")
            if isinstance(major, dict):
                draw = major.get("draw")
                if isinstance(draw, dict):
                    for pic in draw.get("items", []):
                        if isinstance(pic, dict) and isinstance(pic.get("src"), str):
                            source = pic["src"]
                            if is_allowed_media_host(source):
                                normalized = normalize_url(source)
                                if normalized not in images:
                                    images.append(normalized)
                                    segments.append(("image", normalized))
                archive = major.get("archive")
                if isinstance(archive, dict):
                    title = title or str(archive.get("title") or "")
                    cover = normalize_url(str(archive.get("cover") or "")) or None
                    jump_url = archive.get("jump_url")
                    if isinstance(jump_url, str) and jump_url.startswith("http"):
                        url = jump_url
                    bvid = str(archive.get("bvid") or "")
                    if bvid:
                        url = f"https://www.bilibili.com/video/{bvid}"
                opus_data = major.get("opus")
                if isinstance(opus_data, dict):
                    title = title or str(opus_data.get("title") or "")
                    summary = opus_data.get("summary")
                    if isinstance(summary, dict):
                        opus_summary = self._dynamic_rich_text(summary)
                        text = text or opus_summary
                        if opus_summary and not segments:
                            segments.append(("text", opus_summary))
                    for pic in opus_data.get("pics", []):
                        if isinstance(pic, dict) and isinstance(pic.get("url"), str):
                            source = pic["url"]
                            if is_allowed_media_host(source):
                                normalized = normalize_url(source)
                                if normalized not in images:
                                    images.append(normalized)
                                    segments.append(("image", normalized))
                article = major.get("article")
                if isinstance(article, dict):
                    is_article = True
                    title = title or str(article.get("title") or "")
                    raw_covers = article.get("covers")
                    if isinstance(raw_covers, list) and raw_covers:
                        first_cover = raw_covers[0]
                        if isinstance(first_cover, str) and is_allowed_media_host(first_cover):
                            cover = cover or normalize_url(first_cover)
                    raw_cover = article.get("cover")
                    if isinstance(raw_cover, str) and is_allowed_media_host(raw_cover):
                        cover = cover or normalize_url(raw_cover)
                    jump_url = article.get("jump_url")
                    if isinstance(jump_url, str) and jump_url.startswith("http"):
                        url = jump_url
                live_rcmd = major.get("live_rcmd")
                if isinstance(live_rcmd, dict):
                    content = live_rcmd.get("content")
                    if isinstance(content, dict):
                        live_info = content.get("live_play_info")
                        if isinstance(live_info, dict):
                            title = title or str(live_info.get("title") or "")
                            cover = cover or (normalize_url(str(live_info.get("cover") or "")) or None)
        stat = modules.get("module_stat")
        metrics: list[Metric] = []
        if isinstance(stat, dict):
            for label, key in (
                ("点赞", "like"),
                ("评论", "comment"),
                ("分享", "forward"),
            ):
                node = stat.get(key)
                if isinstance(node, dict):
                    metrics.append(metric(label, node.get("count")))
        orig_data = item.get("orig")
        orig_dynamic: BiliDynamicContent | None = None
        if isinstance(orig_data, dict):
            orig_id = str(orig_data.get("id_str") or "")
            parsed_orig = self._build_dynamic(orig_data, orig_id)
            if (
                parsed_orig.text
                or parsed_orig.title
                or parsed_orig.images
                or parsed_orig.cover
                or parsed_orig.author.name
            ):
                orig_dynamic = parsed_orig
        dynamic = BiliDynamicContent(
            url=url,
            title=title or None,
            text=text or None,
            author=author,
            images=images,
            cover=cover,
            metrics=metrics,
            orig=orig_dynamic,
            segments=segments,
            is_article=is_article,
        )
        return dynamic

    def _build_repost_card(self, orig: BiliDynamicContent) -> PreviewCard:
        author = self.create_author(
            orig.author.name or "哔哩哔哩用户",
            clean_cover(orig.author.face),
            description=orig.author.sign or None,
            uid=orig.author.mid or None,
        )
        nested_repost = self._build_repost_card(orig.orig) if orig.orig is not None else None
        timestamp = format_dynamic_time(orig.author.timestamp)
        return PreviewCard(
            platform_name=self.platform.name,
            platform_display_name=self.platform.display_name,
            title=orig.title or "原动态",
            text=orig.text or orig.title or "",
            author=author,
            source_url=orig.url or None,
            cover_url=next(iter(orig.images), orig.cover),
            repost=nested_repost,
            blocks=build_image_blocks(orig.images, limit=self.card_image_limit),
            metrics=tuple(orig.metrics),
            timestamp=timestamp,
        )

    async def _finish_dynamic(self, dynamic: BiliDynamicContent):
        author = self.create_author(
            dynamic.author.name or "哔哩哔哩用户",
            clean_cover(dynamic.author.face),
            description=dynamic.author.sign or None,
            uid=dynamic.author.mid or None,
        )
        repost = self._build_repost_card(dynamic.orig) if dynamic.orig is not None else None
        segments = list(dynamic.segments)
        cover_url = dynamic.cover or next(iter(dynamic.images), None)
        if dynamic.is_article and cover_url is not None:
            if cover_url not in (value for kind, value in segments if kind == "image"):
                segments.insert(0, ("image", cover_url))
        has_mixed_segments = any(k == "image" for k, _ in segments) and any(k == "text" for k, _ in segments)
        use_ordered_segments = bool(segments) and (has_mixed_segments or dynamic.is_article)
        blocks = (
            build_card_blocks(segments, limit=self.card_image_limit)
            if use_ordered_segments
            else build_image_blocks(dynamic.images, limit=self.card_image_limit)
        )
        await self.send_info(
            card_type="common",
            title=dynamic.title or "哔哩哔哩动态",
            author=author,
            text="" if has_mixed_segments else (dynamic.text or ""),
            cover_url=cover_url,
            blocks=blocks,
            url=dynamic.url,
            metrics=dynamic.metrics,
            repost=repost,
        )
        contents: list[ContentItem] = []
        if use_ordered_segments:
            for kind, value in segments:
                if kind == "text":
                    contents.append(self.create_text_content(value))
                elif kind == "image":
                    contents.extend(self.create_image_contents([value], headers=self.media_headers, proxy=self.proxy))
        elif dynamic.images:
            contents.extend(self.create_image_contents(dynamic.images, headers=self.media_headers, proxy=self.proxy))
        elif dynamic.cover:
            contents.extend(self.create_image_contents([dynamic.cover], headers=self.media_headers, proxy=self.proxy))
        elif dynamic.orig is not None:
            if dynamic.orig.images:
                contents.extend(
                    self.create_image_contents(dynamic.orig.images, headers=self.media_headers, proxy=self.proxy)
                )
            elif dynamic.orig.cover:
                contents.extend(
                    self.create_image_contents([dynamic.orig.cover], headers=self.media_headers, proxy=self.proxy)
                )
        return self.result(contents)

    async def _parse_live_by_id(self, room_id: int):
        payload = await self._get_json(
            self.API_LIVE,
            params={"room_id": room_id},
            error="获取直播间信息失败",
        )
        try:
            room = decode_live(payload)
        except (msgspec.ValidationError, ValueError) as exc:
            raise ParseException("直播间信息不可用") from exc
        user = await self._fetch_user(room.uid)
        author = self._build_author(
            user.name if user else str(room.uid),
            user.face if user else "",
            room.uid,
            user,
        )
        status = {0: "未开播", 1: "直播中", 2: "轮播中"}.get(room.live_status, "未开播")
        metrics = [
            metric("在线", room.online),
            metric("关注", room.attention),
        ]
        if room.short_id:
            metrics.append(metric("房间号", room.short_id))
        images = [
            url
            for url in (
                clean_cover(room.keyframe),
                clean_cover(room.user_cover),
                clean_cover(room.cover),
            )
            if url
        ]
        images = list(dict.fromkeys(images))
        contents = self.create_image_contents(images, headers=self.media_headers, proxy=self.proxy)
        text = "\n".join(
            part
            for part in (
                status,
                f"分区: {room.parent_area_name} | {room.area_name}" if room.area_name else None,
                room.description or None,
            )
            if part
        )
        await self.send_info(
            card_type="common",
            title=room.title,
            author=author,
            text=text,
            cover_url=room.cover or room.user_cover or room.keyframe or None,
            url=f"https://live.bilibili.com/{room.room_id}",
            metrics=metrics,
        )
        return self.result(contents)

    async def _parse_favorite_by_id(self, fav_id: int):
        payload = await self._get_json(
            self.API_FAVORITE,
            params={"media_id": fav_id, "pn": 1, "ps": 20},
            error="获取收藏夹失败",
        )
        try:
            fav = decode_favorite(payload)
        except (msgspec.ValidationError, ValueError) as exc:
            raise ParseException("收藏夹内容不可用") from exc
        if not fav.medias:
            raise ParseException("收藏夹内容为空，或被风控")
        author = self.create_author(
            fav.info.upper.name,
            clean_cover(fav.info.upper.face),
            uid=str(fav.info.upper.mid),
        )
        covers = [clean_cover(media.cover) or media.cover for media in fav.medias if media.cover]
        contents: list[MediaContent] = []
        if covers:
            contents = self.create_image_contents(covers, proxy=self.proxy)
        await self.send_info(
            card_type="common",
            title=f"收藏夹 - {fav.info.title}",
            author=author,
            text=fav.info.intro,
            cover_url=next(iter(covers), None),
            url=(f"https://space.bilibili.com/{fav.info.upper.mid}/favlist?fid={fav_id}"),
            metrics=fav.metrics,
            blocks=build_image_blocks(covers, limit=self.card_image_limit),
        )
        return self.result(contents)

    async def _parse_bangumi_by_id(self, kind: str, ident: int):
        params: dict[str, str | int] = {f"{kind}_id": ident}
        payload = await self._get_json(
            self.API_BANGUMI,
            params=params,
            error="获取番剧信息失败",
        )
        try:
            bangumi = decode_bangumi(payload)
        except (msgspec.ValidationError, ValueError) as exc:
            raise ParseException("番剧信息不可用") from exc
        author = self.create_author(
            bangumi.season_title,
            clean_cover(bangumi.square_cover),
        )
        contents = self.create_image_contents(
            [out for out in (clean_cover(bangumi.cover),) if out],
            headers=self.media_headers,
            proxy=self.proxy,
        )
        await self.send_info(
            card_type="common",
            title=bangumi.title,
            author=author,
            text=bangumi.evaluate,
            cover_url=clean_cover(bangumi.cover),
            url=bangumi.share_url,
            metrics=bangumi.stat.metrics,
        )
        return self.result(contents)

    async def _parse_article_by_id(self, read_id: int):
        payload = await self._get_json(
            self.ARTICLE_CARD_API,
            params={"id": read_id},
            error="获取专栏信息失败",
        )
        if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise ParseException(str(payload.get("message") or "专栏不存在"))
        data = payload["data"]
        if not data.get("content") and not data.get("image_urls"):
            raise ParseException("专栏正文为空")
        author_data = data.get("author") if isinstance(data.get("author"), dict) else {}
        stat_data = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        metrics = [
            metric("阅读", stat_data.get("view")),
            metric("点赞", stat_data.get("like")),
            metric("评论", stat_data.get("reply")),
            metric("收藏", stat_data.get("favorite")),
            metric("分享", stat_data.get("share")),
            metric("投币", stat_data.get("coin")),
        ]
        author = self.create_author(
            str(author_data.get("name") or "哔哩哔哩用户"),
            clean_cover(str(author_data.get("face") or "")),
            description=str(author_data.get("sign") or "") or None,
            uid=str(author_data.get("mid") or "") or None,
        )
        image_urls = [
            normalize_url(image)
            for image in data.get("image_urls") or []
            if isinstance(image, str) and is_allowed_media_host(image)
        ]
        contents: list[MediaContent] = []
        if image_urls:
            contents = self.create_image_contents(
                image_urls,
                headers=self.media_headers,
                proxy=self.proxy,
            )
        await self.send_info(
            card_type="common",
            title=str(data.get("title") or "哔哩哔哩专栏"),
            author=author,
            text=str(data.get("summary") or data.get("content") or ""),
            cover_url=next(iter(image_urls), None),
            url=f"https://www.bilibili.com/read/cv{read_id}",
            metrics=metrics,
            blocks=build_image_blocks(image_urls, limit=self.card_image_limit),
        )
        return self.result(contents)
