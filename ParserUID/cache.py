"""ParserUID 缓存定时清理。"""

from gsuid_core.aps import scheduler
from gsuid_core.logger import logger

from .config import cache_retention_seconds
from .render.runtime import clean_render_cache
from .utils.downloader import clean_image_cache


@scheduler.scheduled_job("interval", hours=1)
async def clean_parser_cache() -> None:
    """每小时清理超过保留期的下载缓存和渲染缓存。"""
    max_age_seconds = cache_retention_seconds()
    # 0 表示不清理；此时 cutoff 会等于当前时间，必须提前返回
    if max_age_seconds <= 0:
        return
    removed_downloads = await clean_image_cache(max_age_seconds=max_age_seconds)
    removed_renders = await clean_render_cache(max_age_seconds=max_age_seconds)
    logger.info(
        f"[Parser·缓存] 定时清理完成 | download={removed_downloads} "
        f"| render={removed_renders} | retention_days={max_age_seconds // 86400}"
    )
