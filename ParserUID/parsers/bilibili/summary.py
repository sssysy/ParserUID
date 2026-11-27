"""哔哩哔哩视频 AI 总结解析与格式化。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from .utils import av2bv
from .models import (
    BiliVideoInfo,
    BiliSummaryReply,
    BiliSummaryOutline,
    BiliSummarySubtitle,
    BiliVideoSummaryResult,
)
from ...utils.exceptions import sanitize_error_text

_VIDEO_PATH_RE = re.compile(
    r"/video/(?:(?P<avid>[aA][vV](?P<avid_digits>\d+))|(?P<bvid>[0-9A-Za-z]{8,15}))(?![0-9A-Za-z])",
)
_BARE_BVID_RE = re.compile(r"(?<![0-9A-Za-z])(?P<bvid>BV[0-9A-Za-z]{8,15})(?![0-9A-Za-z])")
_BARE_AVID_RE = re.compile(r"(?<![0-9A-Za-z])[aA][vV](?P<avid_digits>\d+)(?![0-9A-Za-z])")
_HTTP_URL_RE = re.compile(
    r"(?:https?://)?(?:[A-Za-z0-9-]+\.)*(?:bilibili\.com|b23\.tv|bili2233\.cn)/[^\s<>\"'`]+",
)

SUMMARY_SUBTITLE_MAX_CHARS = 4000
SUMMARY_DESC_MAX_CHARS = 1000


def _is_host_or_sub(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def is_bilibili_page_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return _is_host_or_sub(host, "bilibili.com")


def is_short_bilibili_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return _is_host_or_sub(host, "b23.tv") or _is_host_or_sub(host, "bili2233.cn")


def extract_first_bilibili_url(text: str) -> str | None:
    """从输入中提取首个合法的 B 站链接并补全 https 协议头。"""
    matched = _HTTP_URL_RE.search(text.strip())
    if matched is None:
        return None
    candidate = matched.group(0).rstrip(".,;:!?，。；：！？、)>】」』")
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower()
    if not (
        _is_host_or_sub(host, "bilibili.com") or _is_host_or_sub(host, "b23.tv") or _is_host_or_sub(host, "bili2233.cn")
    ):
        return None
    return candidate


def _parse_positive_page(raw_page: str | None) -> int:
    if raw_page is None:
        return 1
    try:
        value = int(raw_page)
    except ValueError:
        return 1
    return value if value > 0 else 1


def extract_video_target_from_url(url: str) -> tuple[str, int] | None:
    """从 bilibili.com 链接提取 (bvid, page)；仅接受可信 B 站域名。"""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not is_bilibili_page_host(url):
        return None
    query = parse_qs(parsed.query)
    page_values = query.get("p")
    page = _parse_positive_page(page_values[0] if page_values else None)

    matched = _VIDEO_PATH_RE.search(parsed.path)
    if matched is not None:
        avid_digits = matched.group("avid_digits")
        if avid_digits is not None:
            return av2bv(int(avid_digits)), page
        bvid = matched.group("bvid")
        if bvid:
            return bvid, page

    bvid_values = query.get("bvid")
    if bvid_values and re.fullmatch(r"[0-9A-Za-z]{8,15}", bvid_values[0]):
        return bvid_values[0], page

    aid_values = query.get("aid")
    if aid_values and aid_values[0].isdigit():
        return av2bv(int(aid_values[0])), page
    return None


def extract_bare_video_id(text: str) -> tuple[str, int] | None:
    """从纯文本提取裸 BV 号或 AV 号。"""
    bvid_match = _BARE_BVID_RE.search(text)
    if bvid_match is not None:
        return bvid_match.group("bvid"), 1
    avid_match = _BARE_AVID_RE.search(text)
    if avid_match is not None:
        return av2bv(int(avid_match.group("avid_digits"))), 1
    return None


def format_seconds(seconds: int) -> str:
    """将秒数格式化为 MM:SS 或 HH:MM:SS。"""
    total = max(0, seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _collect_subtitle_text(subtitles: list[BiliSummarySubtitle] | None) -> str:
    if not subtitles:
        return ""
    lines: list[str] = []
    for group in subtitles:
        for item in group.part_subtitle or ():
            content = item.content.strip()
            if content:
                lines.append(content)
    return "\n".join(lines)


def _status_message_for_reply(reply: BiliSummaryReply) -> tuple[int, str]:
    if reply.code == -101:
        return -1, "未登录 B 站账号，无法获取官方 AI 总结（请先使用 b站登录 或在解析器设置中填写 Cookies）"
    if reply.code == -403:
        return -1, "B 站 AI 总结接口拒绝访问（-403），可能是 Cookie 失效或 WBI 签名未通过"
    if reply.code == -400:
        return -1, "B 站 AI 总结请求参数或 WBI 签名异常（-400）"
    if reply.code != 0:
        detail = sanitize_error_text(reply.message or f"错误码 {reply.code}", hide_local_paths=True)
        return -1, f"B 站 AI 总结接口返回错误：{detail}"

    data = reply.data
    if data is None:
        return -1, "B 站 AI 总结接口未返回数据"
    if data.code == 0:
        return 0, ""
    if data.code == 1:
        stid = (data.stid or "").strip()
        if stid == "":
            return 1, "该视频未识别到可用语音，暂无 B 站官方 AI 总结"
        return 1, "该视频暂未生成 B 站官方 AI 总结"
    if data.code == -1:
        return -1, "该视频不支持 B 站官方 AI 总结（可能无语音、时长不符或内容受限）"
    return data.code, f"B 站 AI 总结暂不可用（状态码 {data.code}）"


def build_summary_result(
    *,
    info: BiliVideoInfo,
    cid: int,
    page: int,
    total_pages: int,
    part_title: str,
    duration: int,
    url: str,
    reply: BiliSummaryReply,
) -> BiliVideoSummaryResult:
    """汇总视频基础信息与 AI 总结接口响应。"""
    data_code, status_message = _status_message_for_reply(reply)
    data = reply.data
    model_result = data.model_result if data is not None else None
    summary = ""
    outline: list[BiliSummaryOutline] = []
    subtitle_text = ""
    result_type = 0
    stid = (data.stid or "").strip() if data is not None else ""

    if model_result is not None:
        result_type = model_result.result_type
        summary = (model_result.summary or "").strip()
        outline = list(model_result.outline or ())
        subtitle_text = _collect_subtitle_text(model_result.subtitle)

    if reply.code == 0 and data_code == 0 and not summary and not outline:
        status_message = "该视频暂无可用的 AI 总结正文"

    return BiliVideoSummaryResult(
        bvid=info.bvid,
        aid=info.aid,
        cid=cid,
        page=page,
        total_pages=total_pages,
        part_title=part_title,
        title=info.title,
        author_name=info.owner.name or "未知UP主",
        author_mid=info.owner.mid,
        duration=duration,
        url=url,
        desc=info.desc.strip(),
        status_code=reply.code,
        data_code=data_code,
        status_message=status_message,
        stid=stid,
        result_type=result_type,
        summary=summary,
        outline=outline,
        subtitle_text=subtitle_text,
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def format_video_summary_text(result: BiliVideoSummaryResult) -> str:
    """生成供 AI 助手阅读与总结的结构化纯文本。"""
    lines: list[str] = [
        f"视频标题：{result.title}",
        f"UP主：{result.author_name}",
        f"BV号：{result.bvid}",
        f"链接：{result.url}",
    ]
    if result.total_pages > 1:
        lines.append(f"分集：P{result.page}/{result.total_pages} - {result.part_title}")
    if result.duration > 0:
        lines.append(f"时长：{format_seconds(result.duration)}")

    if result.status_code == 0 and result.data_code == 0 and (result.summary or result.outline):
        if result.summary:
            lines.append("")
            lines.append("【B站官方 AI 总结】")
            lines.append(result.summary)
        if result.outline:
            lines.append("")
            lines.append("【章节大纲】")
            for section in result.outline:
                section_title = section.title.strip()
                if section_title:
                    lines.append(f"- [{format_seconds(section.timestamp)}] {section_title}")
                for part in section.part_outline or ():
                    part_content = part.content.strip()
                    if part_content:
                        lines.append(f"  · [{format_seconds(part.timestamp)}] {part_content}")
    else:
        lines.append("")
        lines.append(f"【B站官方 AI 总结状态】{result.status_message}")
        if result.subtitle_text:
            lines.append("")
            lines.append("【AI 识别字幕】")
            lines.append(_truncate(result.subtitle_text, SUMMARY_SUBTITLE_MAX_CHARS))

    if result.desc:
        lines.append("")
        lines.append("【视频简介】")
        lines.append(_truncate(result.desc, SUMMARY_DESC_MAX_CHARS))

    return "\n".join(lines)
