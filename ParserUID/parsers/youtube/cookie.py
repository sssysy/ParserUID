"""YouTube Cookie 到 Netscape cookiefile 的安全转换。"""

from __future__ import annotations

import time
import asyncio
import hashlib
from pathlib import Path
from http.cookies import SimpleCookie

from gsuid_core.data_store import get_res_path

COOKIE_DIR = get_res_path("ParserUID") / "cookies"


class YouTubeCookieFile:
    """按 Cookie 内容生成 yt-dlp 可读取的临时 Netscape 文件。"""

    def __init__(self, source: str) -> None:
        self.source = source.strip()
        self.path: Path | None = None
        self._digest = hashlib.sha256(self.source.encode("utf-8")).hexdigest()[:20]

    async def prepare(self) -> Path | None:
        """写入受控 Cookie 目录；空 Cookie 返回 None。"""
        if not self.source:
            return None
        target = COOKIE_DIR / f"youtube_{self._digest}.cookies.txt"
        if not await asyncio.to_thread(target.is_file):
            content = self._netscape()
            if content:
                await asyncio.to_thread(self._write, target, content)
        if await asyncio.to_thread(target.is_file):
            self.path = target
            return target
        return None

    def _netscape(self) -> str:
        raw = self.source
        if raw.lower().startswith("# netscape http cookie file"):
            return raw.rstrip() + "\n"
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return ""
        expires = int(time.time()) + 365 * 24 * 60 * 60
        lines = ["# Netscape HTTP Cookie File", ""]
        for morsel in cookie.values():
            lines.append(
                "\t".join(
                    (
                        ".youtube.com",
                        "TRUE",
                        "/",
                        "TRUE",
                        str(expires),
                        morsel.key,
                        morsel.value,
                    )
                )
            )
        return "\n".join(lines) + "\n" if len(lines) > 2 else ""

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.part")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)

    async def cleanup(self) -> None:
        """删除本次生成的文件；缓存清理之外的显式资源回收。"""
        path = self.path
        if path is not None:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            self.path = None
