"""Google Drive folder watcher for video files.

Provides utilities to:
  - Poll a Drive folder for newly added video files
  - Download files to a local path
  - Persist processing state so files aren't re-processed
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
    "video/mpeg",
    "video/3gpp",
    "video/x-ms-wmv",
    "video/x-flv",
}

STATE_FILE = ".pipeline_state.json"


@dataclass
class DriveFile:
    """Represents a video file in Google Drive."""

    id: str
    name: str
    mime_type: str
    size: int
    created_time: datetime
    web_view_link: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_time"] = self.created_time.isoformat()
        return d


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_service(credentials_path: str):
    """Build and return a Drive API service instance."""
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def _parse_rfc3339(dt_str: str) -> datetime:
    """Parse an RFC 3339 timestamp from the Drive API."""
    # Drive returns e.g. "2026-03-14T09:30:00.000Z"
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def _build_mime_query() -> str:
    """Build a Drive API query fragment that filters for video MIME types."""
    clauses = [f"mimeType = '{mt}'" for mt in sorted(VIDEO_MIME_TYPES)]
    return "(" + " or ".join(clauses) + ")"


def _load_state(state_path: str = STATE_FILE) -> dict:
    """Load pipeline state from disk."""
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(state: dict, state_path: str = STATE_FILE) -> None:
    """Persist pipeline state to disk."""
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _list_video_files(
    service,
    folder_id: str,
    since: datetime | None = None,
) -> list[DriveFile]:
    """List video files in a folder, with optional created-after filter.

    Handles pagination for large folders.
    """
    query_parts = [
        f"'{folder_id}' in parents",
        _build_mime_query(),
        "trashed = false",
    ]
    if since is not None:
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
        query_parts.append(f"createdTime > '{since_str}'")

    query = " and ".join(query_parts)
    fields = "nextPageToken, files(id, name, mimeType, size, createdTime, webViewLink)"

    files: list[DriveFile] = []
    page_token: str | None = None

    while True:
        try:
            resp = (
                service.files()
                .list(
                    q=query,
                    fields=fields,
                    pageSize=100,
                    pageToken=page_token,
                    orderBy="createdTime desc",
                )
                .execute()
            )
        except HttpError as e:
            logger.error("Drive API error listing files: %s", e)
            raise

        for item in resp.get("files", []):
            files.append(
                DriveFile(
                    id=item["id"],
                    name=item["name"],
                    mime_type=item["mimeType"],
                    size=int(item.get("size", 0)),
                    created_time=_parse_rfc3339(item["createdTime"]),
                    web_view_link=item.get("webViewLink", ""),
                )
            )

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return files


# ── Public API ───────────────────────────────────────────────────────────────


def get_new_files(
    folder_id: str,
    since: datetime,
    credentials_path: str = "credentials.json",
) -> list[DriveFile]:
    """Get files added to a Drive folder since a given datetime.

    Parameters
    ----------
    folder_id:
        The Google Drive folder ID to scan.
    since:
        Only return files created after this timestamp.
    credentials_path:
        Path to the Google service-account JSON key file.

    Returns
    -------
    list[DriveFile]
        Video files created after *since*, sorted newest-first.
    """
    service = _get_service(credentials_path)
    return _list_video_files(service, folder_id, since=since)


def download_file(
    file_id: str,
    output_path: str,
    credentials_path: str = "credentials.json",
) -> str:
    """Download a file from Google Drive to a local path.

    Parameters
    ----------
    file_id:
        The Drive file ID.
    output_path:
        Local filesystem path where the file will be saved.
    credentials_path:
        Path to the Google service-account JSON key file.

    Returns
    -------
    str
        The absolute local path of the downloaded file.
    """
    service = _get_service(credentials_path)

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    request = service.files().get_media(fileId=file_id)
    with open(output_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.info("Download %s: %d%%", file_id, int(status.progress() * 100))

    abs_path = str(Path(output_path).resolve())
    logger.info("Downloaded %s → %s", file_id, abs_path)
    return abs_path


def watch_folder(
    folder_id: str,
    credentials_path: str = "credentials.json",
    poll_interval: int = 60,
) -> Generator[DriveFile, None, None]:
    """Watch a Drive folder for new video files, yielding each new file.

    This is a long-running generator that polls at *poll_interval* seconds.
    It persists state in ``.pipeline_state.json`` so that restarting the
    watcher does not re-yield previously seen files.

    Parameters
    ----------
    folder_id:
        The Google Drive folder ID to watch.
    credentials_path:
        Path to the Google service-account JSON key file.
    poll_interval:
        Seconds between polls (default: 60).

    Yields
    ------
    DriveFile
        Each newly discovered video file.
    """
    service = _get_service(credentials_path)

    state = _load_state()
    folder_state = state.get(folder_id, {})
    processed_ids: set[str] = set(folder_state.get("processed_ids", []))
    last_check_str = folder_state.get("last_check")
    last_check = (
        datetime.fromisoformat(last_check_str)
        if last_check_str
        else datetime(2000, 1, 1, tzinfo=timezone.utc)
    )

    logger.info(
        "Watching folder %s (poll=%ds, %d previously processed)",
        folder_id,
        poll_interval,
        len(processed_ids),
    )

    while True:
        try:
            files = _list_video_files(service, folder_id, since=last_check)

            for f in files:
                if f.id not in processed_ids:
                    yield f
                    processed_ids.add(f.id)

            # Update state
            now = datetime.now(timezone.utc)
            state[folder_id] = {
                "last_check": now.isoformat(),
                "processed_ids": sorted(processed_ids),
            }
            _save_state(state)
            last_check = now

        except HttpError as e:
            logger.error("Drive API error during watch: %s", e)
            # Don't crash the watcher; retry on next poll

        except Exception:
            logger.exception("Unexpected error during watch poll")

        time.sleep(poll_interval)
