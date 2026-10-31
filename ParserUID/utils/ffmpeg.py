"""FFmpeg 与多媒体合成工具链。"""

from __future__ import annotations

import json
import uuid
import shutil
import asyncio
from typing import Final
from pathlib import Path
from collections.abc import Sequence

from gsuid_core.logger import logger

_FFMPEG_PATH: str | None = None
_DEFAULT_TIMEOUT: Final = 120.0


class FFmpegUnavailable(RuntimeError):
    """未找到可用的 FFmpeg 可执行文件。"""


def _imageio_ffmpeg_path() -> str | None:
    """查找 imageio-ffmpeg 捆绑的二进制。"""
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        return candidate if isinstance(candidate, str) else None
    except (OSError, RuntimeError, ValueError):
        return None


def get_ffmpeg_path() -> str:
    """获取 FFmpeg 可执行路径，优先系统路径，降级到 imageio-ffmpeg。"""
    global _FFMPEG_PATH
    if _FFMPEG_PATH is not None:
        return _FFMPEG_PATH

    system = shutil.which("ffmpeg")
    if system is not None:
        _FFMPEG_PATH = system
        return system

    bundled = _imageio_ffmpeg_path()
    if bundled is not None and Path(bundled).is_file():
        _FFMPEG_PATH = bundled
        return bundled

    raise FFmpegUnavailable("未检测到 FFmpeg，请安装 FFmpeg 或 imageio-ffmpeg")


def get_ffprobe_path() -> str | None:
    """获取系统 ffprobe 可执行路径。"""
    return shutil.which("ffprobe")


async def exec_ffmpeg_cmd(
    cmd: Sequence[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, bytes, bytes]:
    """异步执行 FFmpeg 命令。"""
    ffmpeg_exe = get_ffmpeg_path()
    full_cmd = [ffmpeg_exe, *cmd[1:]] if cmd and cmd[0] == "ffmpeg" else list(cmd)
    proc = await asyncio.create_subprocess_exec(
        *full_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"FFmpeg 执行超时：{timeout}s") from exc
    return proc.returncode or 0, stdout, stderr


async def probe_duration(path: Path) -> float | None:
    """用 ffprobe 读取媒体时长；未安装 ffprobe 时返回未知。"""
    ffprobe = get_ffprobe_path()
    if ffprobe is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    if proc.returncode != 0:
        return None
    try:
        raw: object = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    format_info = raw.get("format")
    if not isinstance(format_info, dict):
        return None
    duration = format_info.get("duration")
    if isinstance(duration, (int, float, str)):
        try:
            value = float(duration)
        except ValueError:
            return None
        return value if value >= 0 else None
    return None


async def merge_av(
    *,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """合并音视频为单个媒体文件。"""
    target_path = output_path
    if output_path in (video_path, audio_path):
        output_path = output_path.with_name(f"{output_path.stem}_merged{output_path.suffix}")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c",
        "copy",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        str(output_path),
    ]
    code, _, stderr = await exec_ffmpeg_cmd(cmd)
    if code != 0:
        err_msg = stderr.decode("utf-8", errors="ignore")
        logger.warning(f"[媒体] 音视频合并失败：{err_msg}")
        raise RuntimeError(f"音视频合并失败，退出码：{code}")

    if output_path != target_path:
        await asyncio.to_thread(output_path.replace, target_path)
        output_path = target_path
    return output_path


async def merge_to_live_mp4(
    *,
    image_path: Path,
    video_path: Path,
    output_path: Path,
    bgm_path: Path | None = None,
) -> Path:
    """将静态封面与实况片段合成可播放的 Live Photo MP4。"""
    temp_path = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.tmp{output_path.suffix}")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-loop",
        "1",
        "-i",
        str(image_path),
    ]
    if bgm_path is not None:
        cmd.extend(("-i", str(bgm_path)))
    cmd.extend(
        [
            "-map",
            "2:a:0" if bgm_path is not None else "0:a?",
            "-filter_complex",
            "[1:v][0:v]scale2ref=iw:ih[bg][fg];[bg][fg]overlay=shortest=1,format=yuv420p[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(temp_path),
        ]
    )
    code, _, stderr = await exec_ffmpeg_cmd(cmd)
    if code != 0:
        err_msg = stderr.decode("utf-8", errors="ignore")
        logger.warning(f"[媒体] 实况合成失败：{err_msg}")
        raise RuntimeError(f"实况合成失败，退出码：{code}")

    await asyncio.to_thread(temp_path.replace, output_path)
    return output_path
