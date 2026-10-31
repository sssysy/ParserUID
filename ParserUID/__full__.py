from gsuid_core.sv import Plugins

Plugins(
    name="ParserUID",
    prefix=["psr"],
    allow_empty_prefix=False,
)

from . import parser as parser, access_commands as access_commands  # noqa: E402

__all__ = ["access_commands", "parser"]
