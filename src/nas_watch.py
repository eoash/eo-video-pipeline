"""NAS folder watcher using fswatch (macOS FSEvents).

Watches a local/NAS-mounted directory for new video files and yields
them for processing. More responsive than Drive API polling.

Requires fswatch: brew install fswatch
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv",
    ".webm", ".mpeg", ".mpg", ".wmv", ".flv",
}

STATE_FILE = ".nas_watch_state.json"


def _load_state(state_path: str = STATE_FILE) -> set[str]:
    """Load set of already-processed file paths."""
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("processed", []))
    return set()


def _save_state(processed: set[str], state_path: str = STATE_FILE) -> None:
    """Persist processed file paths."""
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"processed": sorted(processed), "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)


def _is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def _is_file_stable(path: str, wait: int = 5) -> bool:
    """Check if a file has finished copying by comparing size over time."""
    try:
        size1 = os.path.getsize(path)
        time.sleep(wait)
        size2 = os.path.getsize(path)
        return size1 == size2 and size1 > 0
    except OSError:
        return False


def scan_existing(watch_dir: str) -> list[str]:
    """Scan directory for existing video files not yet processed."""
    processed = _load_state()
    videos = []
    for entry in Path(watch_dir).iterdir():
        if entry.is_file() and _is_video(str(entry)) and str(entry.resolve()) not in processed:
            videos.append(str(entry.resolve()))
    return sorted(videos)


def watch_directory(
    watch_dir: str,
    poll_fallback: int = 30,
) -> Generator[str, None, None]:
    """Watch a directory for new video files.

    Tries fswatch first (real-time). Falls back to polling if fswatch
    is not available.

    Parameters
    ----------
    watch_dir:
        Directory path to watch (local or NAS mount).
    poll_fallback:
        Polling interval in seconds if fswatch is unavailable.

    Yields
    ------
    str
        Absolute path of each new video file, after it finishes copying.
    """
    watch_path = Path(watch_dir).resolve()
    if not watch_path.is_dir():
        raise NotADirectoryError(f"Watch directory not found: {watch_dir}")

    processed = _load_state()

    # First, yield any existing unprocessed files
    for video_path in scan_existing(str(watch_path)):
        if video_path not in processed:
            logger.info("Found existing unprocessed video: %s", Path(video_path).name)
            yield video_path
            processed.add(video_path)
            _save_state(processed)

    # Try fswatch
    if _has_fswatch():
        logger.info("Using fswatch for real-time monitoring: %s", watch_path)
        yield from _watch_fswatch(str(watch_path), processed)
    else:
        logger.info("fswatch not found, using polling (every %ds): %s", poll_fallback, watch_path)
        yield from _watch_poll(str(watch_path), processed, poll_fallback)


def _has_fswatch() -> bool:
    """Check if fswatch is installed."""
    try:
        subprocess.run(["fswatch", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _watch_fswatch(watch_dir: str, processed: set[str]) -> Generator[str, None, None]:
    """Watch using fswatch (FSEvents on macOS)."""
    proc = subprocess.Popen(
        [
            "fswatch",
            "-r",                    # recursive
            "--event", "Created",    # only new files
            "--event", "Updated",    # catch completed copies
            "-E",                    # extended regex
            "--include", r"\.(mp4|mov|m4v|avi|mkv|webm|mpeg|mpg|wmv|flv)$",
            "--exclude", ".*",       # exclude everything else
            watch_dir,
        ],
        stdout=subprocess.PIPE,
        text=True,
    )

    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            path = line.strip()
            if not path or not os.path.isfile(path):
                continue

            abs_path = str(Path(path).resolve())
            if abs_path in processed:
                continue

            if not _is_video(abs_path):
                continue

            # Wait for file to finish copying
            logger.info("New file detected: %s — waiting for copy to finish...", Path(abs_path).name)
            if not _is_file_stable(abs_path, wait=10):
                logger.warning("File still changing after wait, skipping: %s", abs_path)
                continue

            yield abs_path
            processed.add(abs_path)
            _save_state(processed)

    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _watch_poll(watch_dir: str, processed: set[str], interval: int) -> Generator[str, None, None]:
    """Fallback polling watcher."""
    while True:
        for entry in Path(watch_dir).iterdir():
            if not entry.is_file() or not _is_video(str(entry)):
                continue

            abs_path = str(entry.resolve())
            if abs_path in processed:
                continue

            if not _is_file_stable(abs_path, wait=5):
                continue

            yield abs_path
            processed.add(abs_path)
            _save_state(processed)

        time.sleep(interval)


def mark_processed(video_path: str) -> None:
    """Manually mark a file as processed."""
    processed = _load_state()
    processed.add(str(Path(video_path).resolve()))
    _save_state(processed)
