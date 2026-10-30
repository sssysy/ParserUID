from gsuid_core.sv import Plugins

Plugins(
    name="ParserUID",
    allow_empty_prefix=True,
)

from . import parser  # noqa: E402

__all__ = ["parser"]
