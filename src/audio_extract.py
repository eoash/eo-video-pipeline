"""Extract audio from video files using ffmpeg.

Large video files (>2GB) cannot be uploaded directly to Gemini File API.
This module extracts audio-only tracks, reducing file size dramatically
(e.g., 3.7GB video → 25MB audio).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Gemini File API upload limit
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2GB


def extract_audio(
    video_path: str,
    output_dir: str | None = None,
    bitrate: str = "128k",
) -> str:
    """Extract audio from a video file as m4a (AAC).

    Parameters
    ----------
    video_path:
        Path to the source video file.
    output_dir:
        Directory for the output file. Defaults to same directory as input.
    bitrate:
        Audio bitrate (default: 128k). Higher = better quality, larger file.

    Returns
    -------
    str
        Absolute path to the extracted audio file.

    Raises
    ------
    FileNotFoundError
        If video_path doesn't exist or ffmpeg is not installed.
    RuntimeError
        If ffmpeg exits with an error.
    """
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = src.parent

    audio_path = out_dir / f"{src.stem}_audio.m4a"

    # Skip if already extracted
    if audio_path.exists():
        logger.info("Audio already extracted: %s", audio_path)
        return str(audio_path.resolve())

    logger.info("Extracting audio: %s → %s", src.name, audio_path.name)

    result = subprocess.run(
        [
            "ffmpeg", "-i", str(src),
            "-vn",                    # no video
            "-acodec", "aac",         # AAC codec
            "-b:a", bitrate,          # bitrate
            "-y",                     # overwrite
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=600,  # 10 min max
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")

    size_mb = audio_path.stat().st_size / (1024 * 1024)
    logger.info("Audio extracted: %.1f MB", size_mb)
    return str(audio_path.resolve())


def needs_audio_extraction(video_path: str) -> bool:
    """Check if a video file exceeds the Gemini upload limit."""
    return Path(video_path).stat().st_size > MAX_UPLOAD_BYTES
