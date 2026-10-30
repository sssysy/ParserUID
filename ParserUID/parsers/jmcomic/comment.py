"""jmcomic 评论解析与转换工具。"""

from __future__ import annotations

from collections.abc import Iterable

from .models import JmCommentEntity, JmCommentReplyEntity
from ...utils.utils import format_num
from ...render.models import Comment, CommentReply


def comment_limit(value: object) -> int:
    try:
        limit = int(str(value))
    except (TypeError, ValueError):
        return 5
    return max(0, min(limit, 20))


def decode_comments(
    comments: Iterable[JmCommentEntity],
    *,
    limit: int,
) -> tuple[Comment, ...]:
    """将 jmcomic 评论转换为卡片标准评论结构。"""
    roots = list(comments)[: max(0, limit)]
    return tuple(_build_comment(item) for item in roots)


def _build_comment(item: JmCommentEntity) -> Comment:
    name = (item.nickname or item.username or "JM 用户").strip()
    content = item.content.strip()
    if item.is_spoiler:
        content = f"[剧透] {content}".strip()

    replies: list[CommentReply] = []
    for reply_item in item.replies[:5]:
        replies.append(_build_reply(reply_item, parent_author=name))

    likes = item.likes
    return Comment(
        author_name=name,
        text=content,
        timestamp=item.created_at or None,
        like_count=format_num(likes) if likes > 0 else None,
        reply_count=str(len(replies)) if replies else None,
        replies=tuple(replies),
    )


def _build_reply(reply: JmCommentReplyEntity, parent_author: str) -> CommentReply:
    name = (reply.nickname or reply.username or "JM 用户").strip()
    content = reply.content.strip()
    if reply.is_spoiler:
        content = f"[剧透] {content}".strip()
    return CommentReply(
        author_name=name,
        text=content,
        parent_author=parent_author,
    )
