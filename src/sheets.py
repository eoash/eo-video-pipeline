"""Google Sheets writer for video analysis results.

Writes structured analysis data across four sheets:
  - Index: one row per video (summary-level metadata)
  - Key Moments: all key moments across videos
  - Segments: topic segments with start/end timecodes
  - Chapters: suggested chapter markers
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Sheet names
SHEET_INDEX = "Index"
SHEET_KEY_MOMENTS = "Key Moments"
SHEET_SEGMENTS = "Segments"
SHEET_CHAPTERS = "Chapters"

# Headers for each sheet (used when initialising a fresh spreadsheet)
HEADERS = {
    SHEET_INDEX: [
        "Video Name", "Date", "Duration", "Summary", "Drive Link", "Speakers",
    ],
    SHEET_KEY_MOMENTS: [
        "Video Name", "Timecode", "Speaker", "Quote", "Topic", "Emotion", "Importance",
    ],
    SHEET_SEGMENTS: [
        "Video Name", "Start", "End", "Topic", "Summary",
    ],
    SHEET_CHAPTERS: [
        "Video Name", "Timecode", "Title (KO)", "Title",
    ],
}


def _get_service(credentials_path: str):
    """Build and return a Sheets API service instance."""
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _format_timecode(seconds: float | int | str) -> str:
    """Convert seconds (or an already-formatted string) to HH:MM:SS."""
    if isinstance(seconds, str):
        # Already formatted – return as-is
        return seconds
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ensure_sheet_exists(service, spreadsheet_id: str, sheet_name: str) -> None:
    """Create a sheet tab if it doesn't already exist."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if sheet_name not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {"addSheet": {"properties": {"title": sheet_name}}}
                ]
            },
        ).execute()
        logger.info("Created sheet tab: %s", sheet_name)


def _sheet_is_empty(service, spreadsheet_id: str, sheet_name: str) -> bool:
    """Return True if a sheet has no data at all."""
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'!A1:A1")
        .execute()
    )
    return not result.get("values")


def _ensure_headers(service, spreadsheet_id: str, sheet_name: str) -> None:
    """Write header row if the sheet is empty."""
    if _sheet_is_empty(service, spreadsheet_id, sheet_name):
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS[sheet_name]]},
        ).execute()
        logger.info("Wrote headers to: %s", sheet_name)


def _append_rows(
    service, spreadsheet_id: str, sheet_name: str, rows: list[list[Any]]
) -> None:
    """Append rows to the bottom of a sheet (after existing data)."""
    if not rows:
        return
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    logger.info("Appended %d rows to %s", len(rows), sheet_name)


# ── Public API ───────────────────────────────────────────────────────────────


def write_analysis(
    spreadsheet_id: str,
    video_name: str,
    analysis: dict,
    credentials_path: str = "credentials.json",
) -> str:
    """Write analysis results to Google Sheets. Returns the sheet URL.

    Parameters
    ----------
    spreadsheet_id:
        The ID of the target Google Spreadsheet.
    video_name:
        Human-readable name for the video (used as a key across sheets).
    analysis:
        An ``AnalysisResult``-shaped dict with optional keys:
        ``summary``, ``duration``, ``drive_link``, ``speakers``,
        ``key_moments``, ``segments``, ``chapters``.
    credentials_path:
        Path to the Google service-account JSON key file.

    Returns
    -------
    str
        The URL of the spreadsheet.
    """
    service = _get_service(credentials_path)

    # Ensure all four sheet tabs exist and have headers
    for sheet_name in HEADERS:
        _ensure_sheet_exists(service, spreadsheet_id, sheet_name)
        _ensure_headers(service, spreadsheet_id, sheet_name)

    today = datetime.now().strftime("%Y-%m-%d")

    # ── Sheet 1: Index ───────────────────────────────────────────────────
    duration_raw = analysis.get("duration", "")
    duration = _format_timecode(duration_raw) if duration_raw != "" else ""
    speakers = analysis.get("speakers", [])
    speakers_str = ", ".join(speakers) if isinstance(speakers, list) else str(speakers)

    _append_rows(service, spreadsheet_id, SHEET_INDEX, [
        [
            video_name,
            today,
            duration,
            analysis.get("summary", ""),
            analysis.get("drive_link", ""),
            speakers_str,
        ]
    ])

    # ── Sheet 2: Key Moments ─────────────────────────────────────────────
    key_moments_rows: list[list[Any]] = []
    for km in analysis.get("key_moments", []):
        key_moments_rows.append([
            video_name,
            _format_timecode(km.get("timecode", 0)),
            km.get("speaker", ""),
            km.get("quote", ""),
            km.get("topic", ""),
            km.get("emotion", ""),
            km.get("importance", ""),
        ])
    _append_rows(service, spreadsheet_id, SHEET_KEY_MOMENTS, key_moments_rows)

    # ── Sheet 3: Segments ────────────────────────────────────────────────
    segments_rows: list[list[Any]] = []
    for seg in analysis.get("segments", []):
        segments_rows.append([
            video_name,
            _format_timecode(seg.get("start", 0)),
            _format_timecode(seg.get("end", 0)),
            seg.get("topic", ""),
            seg.get("summary", ""),
        ])
    _append_rows(service, spreadsheet_id, SHEET_SEGMENTS, segments_rows)

    # ── Sheet 4: Chapters ────────────────────────────────────────────────
    chapters_rows: list[list[Any]] = []
    for ch in analysis.get("chapters", []):
        chapters_rows.append([
            video_name,
            _format_timecode(ch.get("timecode", 0)),
            ch.get("title_ko", ""),
            ch.get("title", ""),
        ])
    _append_rows(service, spreadsheet_id, SHEET_CHAPTERS, chapters_rows)

    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
    logger.info("Analysis written → %s", url)
    return url
