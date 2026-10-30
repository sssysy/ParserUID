from gsuid_core.sv import Plugins

Plugins(
    name="ParserUID",
    allow_empty_prefix=True,
)

from . import (  # noqa: E402
    parser as parser,
    webconsole as webconsole,
)
