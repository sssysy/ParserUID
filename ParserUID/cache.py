"""ParserUID 缓存定时清理。"""

from gsuid_core.aps import scheduler
from gsuid_core.logger import logger

from .config import cache_retention_seconds, cache_cleanup_hour_minute
from .render.runtime import clean_render_cache
from .utils.downloader import clean_image_cache


@scheduler.scheduled_job(
    "cron",
    hour=cache_cleanup_hour_minute()[0],
    minute=cache_cleanup_hour_minute()[1],
)
async def clean_parser_cache() -> None:
    """每天清理超过保留期的下载缓存和渲染缓存。"""
    max_age_seconds = cache_retention_seconds()
    removed_downloads = await clean_image_cache(max_age_seconds=max_age_seconds)
    removed_renders = await clean_render_cache(max_age_seconds=max_age_seconds)
    logger.info(
        f"[Parser·缓存] 定时清理完成 | download={removed_downloads} "
        f"| render={removed_renders} | retention_days={max_age_seconds // 86400}"
    )
