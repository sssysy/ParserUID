"""皮肤包目录扫描、Manifest 校验与运行时定位。"""

from __future__ import annotations

import json
from typing import Final, Literal, TypeAlias
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Mapping

from gsuid_core.logger import logger

from ..config import THIRDPARTY_SKINS_DIR

BUILTIN_SKIN_DIR: Final = Path(__file__).resolve().parent / "pack"
_SKIN_NAME_ALLOWED: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

SkinSource: TypeAlias = Literal["builtin", "thirdparty"]


@dataclass(frozen=True, slots=True)
class SkinManifest:
    """一个皮肤包 manifest 的稳定运行时表示。"""

    name: str
    display_name: str
    templates: Mapping[str, str]
    version: str = ""


@dataclass(frozen=True, slots=True)
class SkinPackage:
    """已完成结构校验的皮肤包。"""

    name: str
    source: SkinSource
    pack_dir: Path
    manifest: SkinManifest


def get_thirdparty_skins_dir() -> Path:
    """返回第三方皮肤数据根目录并确保其存在。"""
    path = THIRDPARTY_SKINS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def skin_name_is_safe(skin_name: str) -> bool:
    """只允许简单目录名，拒绝路径分隔符和上级目录。"""
    return bool(skin_name) and all(char in _SKIN_NAME_ALLOWED for char in skin_name)


def _manifest_string(raw: Mapping[str, object], key: str) -> str:
    if key not in raw:
        raise ValueError(f"皮肤 manifest 缺少字段：{key}")
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"皮肤 manifest 字段必须是字符串：{key}")
    return value.strip()


def _manifest_templates(raw: Mapping[str, object]) -> Mapping[str, str]:
    if "templates" not in raw:
        raise ValueError("皮肤 manifest 缺少 templates")
    value = raw["templates"]
    if not isinstance(value, dict):
        raise ValueError("皮肤 manifest 的 templates 必须是对象")
    templates: dict[str, str] = {}
    for kind, template_name in value.items():
        if not isinstance(kind, str) or not isinstance(template_name, str):
            raise ValueError("皮肤 templates 的键和值必须是字符串")
        relative = Path(template_name)
        if (
            not template_name
            or relative.is_absolute()
            or relative.suffix.lower() != ".html"
            or ".." in relative.parts
            or len(relative.parts) != 1
        ):
            raise ValueError(f"皮肤模板必须是目录内的 .html 文件名：{template_name}")
        templates[kind] = template_name
    if "default" not in templates:
        raise ValueError("皮肤 templates 必须包含 default")
    return templates


def load_skin_manifest(pack_dir: Path) -> SkinManifest:
    """读取并校验单个皮肤 manifest。"""
    manifest_file = pack_dir / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError(f"皮肤缺少 manifest.json：{pack_dir.name}")
    raw: object = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"皮肤 manifest 必须是对象：{pack_dir.name}")
    templates = _manifest_templates(raw)
    return SkinManifest(
        name=_manifest_string(raw, "name"),
        display_name=_manifest_string(raw, "display_name"),
        version=_manifest_string(raw, "version"),
        templates=templates,
    )


def _load_skin_package(pack_dir: Path, source: SkinSource) -> SkinPackage | None:
    try:
        manifest = load_skin_manifest(pack_dir)
        if pack_dir.name != manifest.name:
            raise ValueError(f"皮肤目录名与 manifest.name 不一致：{pack_dir.name} != {manifest.name}")
        for template_name in manifest.templates.values():
            if not (pack_dir / template_name).is_file():
                raise ValueError(f"皮肤模板文件不存在：{template_name}")
    except (OSError, ValueError) as exc:
        logger.warning(
            f"[Parser·皮肤] 忽略无效皮肤包 | name={pack_dir.name} | source={source} | error_type={type(exc).__name__}",
        )
        return None
    return SkinPackage(
        name=manifest.name,
        source=source,
        pack_dir=pack_dir,
        manifest=manifest,
    )


def _scan_skin_root(
    root_dir: Path,
    source: SkinSource,
    packages: dict[str, SkinPackage],
) -> None:
    if not root_dir.is_dir():
        return
    try:
        resolved_root = root_dir.resolve(strict=True)
    except OSError as exc:
        logger.warning(f"[Parser·皮肤] 皮肤根目录无效 | source={source} | error_type={type(exc).__name__}")
        return
    for child in sorted(root_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        try:
            resolved_child = child.resolve(strict=True)
        except OSError as exc:
            logger.warning(
                f"[Parser·皮肤] 皮肤目录无效 | name={child.name} | source={source} | error_type={type(exc).__name__}",
            )
            continue
        if not resolved_child.is_relative_to(resolved_root):
            logger.warning(f"[Parser·皮肤] 皮肤目录越界被拒绝 | name={child.name} | source={source}")
            continue
        package = _load_skin_package(resolved_child, source)
        if package is None:
            continue
        existing = packages.get(package.name)
        if existing is not None:
            logger.warning(
                f"[Parser·皮肤] 同名皮肤被拒绝 | name={package.name} "
                f"| existing={existing.source} | candidate={package.source}",
            )
            continue
        packages[package.name] = package


def scan_skin_packs(*, builtin_dir: Path = BUILTIN_SKIN_DIR) -> dict[str, SkinPackage]:
    """扫描内置和第三方皮肤目录，返回通过校验的候选清单。"""
    packages: dict[str, SkinPackage] = {}
    _scan_skin_root(builtin_dir, "builtin", packages)
    _scan_skin_root(get_thirdparty_skins_dir(), "thirdparty", packages)
    return packages


def resolve_skin_pack(
    skin_name: str,
    *,
    builtin_dir: Path = BUILTIN_SKIN_DIR,
) -> SkinPackage:
    """按名称解析内置或第三方皮肤包。"""
    if not skin_name_is_safe(skin_name):
        raise ValueError(f"不支持的皮肤：{skin_name}")
    package = scan_skin_packs(builtin_dir=builtin_dir).get(skin_name)
    if package is None:
        raise ValueError(f"不支持的皮肤：{skin_name}")
    return package
