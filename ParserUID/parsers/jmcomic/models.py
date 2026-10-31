"""jmcomic 客户端协议与接口实体类型。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from collections.abc import Sequence


@runtime_checkable
class JmCommentReplyEntity(Protocol):
    nickname: str
    username: str
    user_id: str
    content: str
    is_spoiler: bool
    likes: int
    created_at: str


@runtime_checkable
class JmCommentEntity(Protocol):
    nickname: str
    username: str
    user_id: str
    content: str
    is_spoiler: bool
    likes: int
    created_at: str
    replies: Sequence[JmCommentReplyEntity]


@runtime_checkable
class JmPhotoItem(Protocol):
    img_url: str
    scramble_id: str


@runtime_checkable
class JmPhotoDetail(Protocol):
    sort: str

    def __iter__(self) -> Sequence[JmPhotoItem]: ...
    def __len__(self) -> int: ...


@runtime_checkable
class JmAlbumDetail(Protocol):
    title: str
    authoroname: str
    actors: Sequence[str]
    description: str
    tags: Sequence[str]
    views: int
    likes: int
    episode_list: Sequence[object]


@runtime_checkable
class JmAsyncClientProtocol(Protocol):
    async def get_album_detail(self, comic_id: str) -> JmAlbumDetail: ...
    async def get_photo_detail(self, comic_id: str) -> JmPhotoDetail: ...
    async def album_pagination(
        self,
        comic_id: str,
        page: int = 1,
        need_total: bool = False,
    ) -> Sequence[JmCommentEntity]: ...
    async def __aenter__(self) -> JmAsyncClientProtocol: ...
    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None: ...


@runtime_checkable
class JmOptionProtocol(Protocol):
    def new_jm_async_client(
        self,
        *,
        proxies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> JmAsyncClientProtocol: ...
