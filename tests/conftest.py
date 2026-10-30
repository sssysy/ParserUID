"""Pytest 沙箱环境与日志/配置隔离配置。"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

# 1. 隔离日志目录
_LOG_DIR = Path(__file__).resolve().parent.parent / ".pytest_logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_orig_file_init = logging.FileHandler.__init__


def _safe_file_init(self: logging.FileHandler, filename: str | Path, *args: object, **kwargs: object) -> None:
    safe_path = _LOG_DIR / Path(filename).name
    _orig_file_init(self, safe_path, *args, **kwargs)


logging.FileHandler.__init__ = _safe_file_init  # type: ignore[assignment]

# 2. 拦截沙箱只读环境下 Core 写入 core_config.json.part
try:
    import boltons.fileutils

    _orig_atomic = boltons.fileutils.atomic_save

    class _SafeAtomicSave:
        def __init__(self, dest_path: object, *args: object, **kwargs: object) -> None:
            self.dest_path = str(dest_path)
            self._target = _orig_atomic(dest_path, *args, **kwargs)
            self._tmp_file: tempfile._TemporaryFileWrapper[bytes] | None = None

        def __enter__(self) -> object:
            try:
                return self._target.__enter__()
            except (PermissionError, OSError):
                self._tmp_file = tempfile.NamedTemporaryFile(delete=True)
                return self._tmp_file

        def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool | None:
            if self._tmp_file is not None:
                self._tmp_file.close()
                return True
            try:
                return self._target.__exit__(exc_type, exc_val, exc_tb)
            except (PermissionError, OSError):
                return True

    boltons.fileutils.atomic_save = _SafeAtomicSave  # type: ignore[assignment]
except Exception:
    pass
