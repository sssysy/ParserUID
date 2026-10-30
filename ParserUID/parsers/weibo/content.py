"""微博正文与文章 HTML 富文本清洗工具。"""

from __future__ import annotations

import re
import html

from bs4 import Tag, BeautifulSoup
from bs4.element import Comment, PageElement, NavigableString

_NEWLINE_TAGS = frozenset({"br", "p", "div", "li", "blockquote"})
_TRAILING_SHORT_URL = re.compile(r"\s*https?://t\.cn/\S+\s*$")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_BROKEN_NBSP = re.compile(r"(?m)^\s*xa0\s*$")


def _attr(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value or "")


def _unescape(value: str) -> str:
    current = value
    for _ in range(2):
        unescaped = html.unescape(current)
        if unescaped == current:
            break
        current = unescaped
    return current


def html_to_text(value: str) -> str:
    """将微博富文本 HTML 转换为排版清晰的纯文本。"""
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    parts: list[str] = []

    def walk(node: BeautifulSoup | PageElement) -> None:
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            parts.append(_unescape(str(node)))
            return
        if not isinstance(node, Tag):
            return
        if node.name in _NEWLINE_TAGS:
            parts.append("\n")
        if node.name == "img":
            parts.append(_attr(node.get("alt")))
            return
        if node.name == "a":
            label = node.get_text(" ", strip=True)
            href = _attr(node.get("href"))
            if label:
                parts.append(label)
            elif href:
                parts.append(href)
            return
        for child in node.children:
            walk(child)

    walk(soup)
    text = "".join(parts).replace("\u200b", "").replace("\xa0", " ")
    text = _BROKEN_NBSP.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _MULTI_NEWLINE.sub("\n\n", text).strip()


def clean_status_text(html_text: str, raw_text: str) -> str:
    """清洗微博文本并去除末尾自动追加的短链接。"""
    text = html_to_text(html_text) or html_to_text(raw_text)
    return _TRAILING_SHORT_URL.sub("", text).strip()


def parse_article_blocks(content: str) -> tuple[list[tuple[str, str | None]], str]:
    """将微博头条文章解析为有序的文字与图片块。"""
    soup = BeautifulSoup(content or "", "html.parser")
    blocks: list[tuple[str, str | None]] = []
    pending: list[str] = []

    def flush() -> None:
        text = html_to_text("<br>".join(pending))
        pending.clear()
        if text:
            blocks.append(("text", text))

    for element in soup.find_all(["p", "img"]):
        if not isinstance(element, Tag):
            continue
        if element.name == "p":
            text = html_to_text(str(element))
            if text:
                pending.append(text)
            continue
        src = _attr(element.get("src") or element.get("data-src"))
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        flush()
        blocks.append(("image", src))
    flush()
    text = "\n\n".join(value for kind, value in blocks if kind == "text" and value)
    return blocks, text


def comment_text(value: object) -> str:
    return html_to_text(str(value or ""))
