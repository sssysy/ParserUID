from gsuid_core.sv import Plugins

Plugins(
    name="ParserUID",
    prefix=["psr"],
    allow_empty_prefix=False,
)

from . import (  # noqa: E402
    parser as parser,
    webconsole as webconsole,
    access_commands as access_commands,
)
