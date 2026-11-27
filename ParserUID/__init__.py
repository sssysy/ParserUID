from gsuid_core.sv import Plugins

Plugins(
    name="ParserUID",
    force_prefix=["psr"],
    allow_empty_prefix=False,
)

from .register import register_backup_hints  # noqa: E402

register_backup_hints()

from . import (  # noqa: E402
    help as help,
    cache as cache,
    parser as parser,
    ai_runtime as ai_runtime,
    extra_info as extra_info,
    webconsole as webconsole,
    access_commands as access_commands,
)
