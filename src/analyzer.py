"""
Gemini-powered interview video analyzer for EO Studio.

Analyzes raw interview footage and extracts structured data:
transcript, key moments, topic segments, chapter suggestions.

Uses the google-genai SDK (unified SDK) with Gemini File API.

Usage:
    import asyncio
    from analyzer import analyze_video

    result = asyncio.run(analyze_video("interview.mp4", api_key="..."))
    print(result.summary)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KeyMoment:
    time_seconds: float
    speaker: str
    quote: str
    topic: str
    emotion: str  # e.g., "passionate", "reflective", "humorous"
    importance: int  # 1-5, 5 being most important


@dataclass
class TopicSegment:
    start_seconds: float
    end_seconds: float
    topic: str
    summary: str


@dataclass
class ChapterSuggestion:
    start_seconds: float
    title: str
    title_ko: str


@dataclass
class AnalysisResult:
    transcript: str
    key_moments: list[KeyMoment]
    topic_segments: list[TopicSegment]
    chapter_suggestions: list[ChapterSuggestion]
    summary: str
    speakers: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_timestamp(ts: str) -> float:
    """Convert 'HH:MM:SS', 'MM:SS', or 'SS' string to seconds.

    >>> parse_timestamp("01:23:45")
    5025.0
    >>> parse_timestamp("12:30")
    750.0
    >>> parse_timestamp("45")
    45.0
    """
    parts = ts.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 1:
        return parts[0]
    raise ValueError(f"Cannot parse timestamp: {ts!r}")


def _seconds_to_hms(seconds: float) -> str:
    """Format seconds as HH:MM:SS for display."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """\
You are an expert video editor and content strategist for EO Studio (이오),
a Korean media company that produces interview-format YouTube content about
startups, technology, and business.

Analyze this interview video and return a JSON object with the following
structure. All text fields must be in Korean unless noted otherwise.

{
  "transcript": "<Full Korean transcript with speaker labels and timestamps in [MM:SS] format>",
  "speakers": ["<speaker 1 name or role>", "<speaker 2 name or role>"],
  "summary": "<3-5 sentence summary of the interview in Korean>",
  "key_moments": [
    {
      "time": "<HH:MM:SS or MM:SS>",
      "speaker": "<speaker name>",
      "quote": "<exact Korean quote, max 2 sentences>",
      "topic": "<topic keyword in Korean>",
      "emotion": "<one of: passionate, reflective, humorous, surprising, emotional, confident, vulnerable>",
      "importance": <1-5, 5 = most impactful/quotable>
    }
  ],
  "topic_segments": [
    {
      "start": "<HH:MM:SS or MM:SS>",
      "end": "<HH:MM:SS or MM:SS>",
      "topic": "<segment topic in Korean>",
      "summary": "<1-2 sentence segment summary in Korean>"
    }
  ],
  "chapter_suggestions": [
    {
      "start": "<HH:MM:SS or MM:SS>",
      "title": "<English chapter title, punchy YouTube style>",
      "title_ko": "<Korean chapter title>"
    }
  ]
}

Guidelines:
- This is a Korean-language interview (single interviewer + interviewee).
- The final edited video will be ~15 minutes cut from this raw footage.
- Identify the TOP 20 most impactful, quotable, or emotionally compelling moments.
- For key_moments, prefer moments that would make great short-form clips or thumbnails.
- Chapter suggestions should follow EO style: 4-6 chapters, each covering 2-5 minutes
  of the final edit. Titles should be intriguing and clickworthy.
- For speakers, use their actual name if mentioned, otherwise use "인터뷰어" / "게스트".
- Timestamps must be accurate to the video timeline.
- Return ONLY valid JSON, no markdown fences or extra text.
"""

CHUNK_PROMPT_PREFIX = """\
This is segment {segment_num} of {total_segments} from a longer interview video
(segment covers approximately {start_time} to {end_time} of the full video).

"""

MERGE_PROMPT = """\
You are merging analysis results from {n} consecutive segments of the same
interview video. The segments were analyzed independently.

Combine them into a single coherent analysis. Rules:
- Merge transcripts in order, keeping timestamps from the original segments.
- Deduplicate key_moments if the same quote appears in overlapping boundaries.
  Keep the TOP 20 most important moments overall.
- Merge topic_segments: if consecutive segments share the same topic, merge them
  into one segment.
- Create NEW chapter_suggestions (4-6 chapters) for the full interview, not
  just concatenation.
- Write a NEW overall summary covering the entire interview.
- Speakers list should be the union of all segments.

Segment analyses (JSON array):
{segments_json}

Return a single merged JSON object with the same schema as each segment.
Return ONLY valid JSON, no markdown fences or extra text.
"""


# ---------------------------------------------------------------------------
# File upload helpers
# ---------------------------------------------------------------------------

UPLOAD_POLL_INTERVAL = 5  # seconds
UPLOAD_TIMEOUT = 600  # 10 minutes max wait for processing


async def _upload_video(client: genai.Client, video_path: str) -> types.File:
    """Upload a video file to Gemini File API and wait until it's ACTIVE."""
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    logger.info("Uploading %s (%d MB)...", path.name, path.stat().st_size // (1024 * 1024))

    uploaded = await asyncio.to_thread(
        client.files.upload, file=str(path)
    )

    # Poll until the file is processed (state == ACTIVE)
    start = time.monotonic()
    while uploaded.state != "ACTIVE":
        if uploaded.state == "FAILED":
            raise RuntimeError(f"File upload failed: {uploaded.name}")
        elapsed = time.monotonic() - start
        if elapsed > UPLOAD_TIMEOUT:
            raise TimeoutError(
                f"File {uploaded.name} not ready after {UPLOAD_TIMEOUT}s "
                f"(state={uploaded.state})"
            )
        logger.info(
            "Waiting for file processing... state=%s (%.0fs elapsed)",
            uploaded.state, elapsed,
        )
        await asyncio.sleep(UPLOAD_POLL_INTERVAL)
        uploaded = await asyncio.to_thread(client.files.get, name=uploaded.name)

    logger.info("File ready: %s", uploaded.name)
    return uploaded


async def _cleanup_file(client: genai.Client, file: types.File) -> None:
    """Delete an uploaded file from Gemini (best-effort)."""
    try:
        await asyncio.to_thread(client.files.delete, name=file.name)
        logger.info("Cleaned up uploaded file: %s", file.name)
    except Exception:
        logger.warning("Failed to clean up file %s (non-fatal)", file.name, exc_info=True)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        # Remove closing fence
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_analysis_json(raw: str) -> dict:
    """Parse the JSON response from Gemini, tolerating markdown fences."""
    cleaned = _strip_json_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Gemini response as JSON:\n%s", cleaned[:500])
        raise ValueError(f"Gemini returned invalid JSON: {exc}") from exc


def _dict_to_result(data: dict) -> AnalysisResult:
    """Convert a parsed JSON dict into an AnalysisResult dataclass."""
    key_moments = [
        KeyMoment(
            time_seconds=parse_timestamp(m["time"]),
            speaker=m.get("speaker", ""),
            quote=m.get("quote", ""),
            topic=m.get("topic", ""),
            emotion=m.get("emotion", ""),
            importance=int(m.get("importance", 3)),
        )
        for m in data.get("key_moments", [])
    ]

    topic_segments = [
        TopicSegment(
            start_seconds=parse_timestamp(s["start"]),
            end_seconds=parse_timestamp(s["end"]),
            topic=s.get("topic", ""),
            summary=s.get("summary", ""),
        )
        for s in data.get("topic_segments", [])
    ]

    chapter_suggestions = [
        ChapterSuggestion(
            start_seconds=parse_timestamp(c["start"]),
            title=c.get("title", ""),
            title_ko=c.get("title_ko", ""),
        )
        for c in data.get("chapter_suggestions", [])
    ]

    return AnalysisResult(
        transcript=data.get("transcript", ""),
        key_moments=key_moments,
        topic_segments=topic_segments,
        chapter_suggestions=chapter_suggestions,
        summary=data.get("summary", ""),
        speakers=data.get("speakers", []),
    )


# ---------------------------------------------------------------------------
# Single-segment analysis
# ---------------------------------------------------------------------------

MODEL = "gemini-2.0-flash"
MAX_SEGMENT_SECONDS = 30 * 60  # 30 minutes


async def _analyze_single(
    client: genai.Client,
    file_ref: types.File,
    prompt: str,
) -> dict:
    """Run a single Gemini generate_content call and return parsed JSON."""
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=MODEL,
        contents=[file_ref, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )

    if not response.text:
        raise RuntimeError("Gemini returned an empty response")

    return _parse_analysis_json(response.text)


# ---------------------------------------------------------------------------
# Chunked analysis for long videos
# ---------------------------------------------------------------------------

def _estimate_duration_seconds(video_path: str) -> float | None:
    """Try to estimate video duration using ffprobe. Returns None on failure."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


async def _analyze_chunked(
    client: genai.Client,
    file_ref: types.File,
    duration_seconds: float,
) -> AnalysisResult:
    """Split a long video into segments, analyze each, then merge."""
    num_segments = math.ceil(duration_seconds / MAX_SEGMENT_SECONDS)
    logger.info(
        "Video is %.0f min — splitting into %d segments for analysis",
        duration_seconds / 60, num_segments,
    )

    segment_results: list[dict] = []
    for i in range(num_segments):
        start = i * MAX_SEGMENT_SECONDS
        end = min((i + 1) * MAX_SEGMENT_SECONDS, duration_seconds)
        segment_prompt = CHUNK_PROMPT_PREFIX.format(
            segment_num=i + 1,
            total_segments=num_segments,
            start_time=_seconds_to_hms(start),
            end_time=_seconds_to_hms(end),
        ) + ANALYSIS_PROMPT

        logger.info("Analyzing segment %d/%d (%s - %s)...", i + 1, num_segments,
                     _seconds_to_hms(start), _seconds_to_hms(end))
        result = await _analyze_single(client, file_ref, segment_prompt)
        segment_results.append(result)

    # Merge segments
    if len(segment_results) == 1:
        return _dict_to_result(segment_results[0])

    logger.info("Merging %d segment results...", len(segment_results))
    merge_prompt = MERGE_PROMPT.format(
        n=len(segment_results),
        segments_json=json.dumps(segment_results, ensure_ascii=False, indent=2),
    )

    merged = await asyncio.to_thread(
        client.models.generate_content,
        model=MODEL,
        contents=[merge_prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    if not merged.text:
        raise RuntimeError("Gemini returned empty merge response")

    merged_data = _parse_analysis_json(merged.text)
    return _dict_to_result(merged_data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def analyze_video(video_path: str, api_key: str) -> AnalysisResult:
    """Upload video to Gemini and get full structured analysis.

    For videos longer than 1 hour, automatically splits into 30-minute
    segments, analyzes each, and merges the results.

    Args:
        video_path: Path to a local video file.
        api_key: Google Gemini API key.

    Returns:
        AnalysisResult with transcript, key moments, topic segments,
        chapter suggestions, summary, and speaker list.

    Raises:
        FileNotFoundError: If video_path doesn't exist.
        RuntimeError: If the Gemini API returns an error or empty response.
        TimeoutError: If the file upload takes too long to process.
        ValueError: If the API response isn't valid JSON.
    """
    client = genai.Client(api_key=api_key)
    file_ref = await _upload_video(client, video_path)

    try:
        duration = _estimate_duration_seconds(video_path)

        if duration and duration > MAX_SEGMENT_SECONDS * 2:
            # Long video — chunked analysis
            return await _analyze_chunked(client, file_ref, duration)

        # Standard single-pass analysis
        logger.info("Analyzing video (single pass)...")
        data = await _analyze_single(client, file_ref, ANALYSIS_PROMPT)
        return _dict_to_result(data)

    finally:
        await _cleanup_file(client, file_ref)


async def analyze_uploaded_file(
    file_name: str,
    api_key: str,
    duration_seconds: float | None = None,
) -> AnalysisResult:
    """Analyze an already-uploaded Gemini file by its resource name.

    Use this when the video has already been uploaded via the File API
    (e.g., file_name="files/abc123").

    Args:
        file_name: Gemini file resource name (e.g., "files/abc123").
        api_key: Google Gemini API key.
        duration_seconds: Optional video duration for chunked analysis.

    Returns:
        AnalysisResult with the same structure as analyze_video().
    """
    client = genai.Client(api_key=api_key)

    file_ref = await asyncio.to_thread(client.files.get, name=file_name)
    if file_ref.state != "ACTIVE":
        raise RuntimeError(
            f"File {file_name} is not ready (state={file_ref.state})"
        )

    if duration_seconds and duration_seconds > MAX_SEGMENT_SECONDS * 2:
        return await _analyze_chunked(client, file_ref, duration_seconds)

    data = await _analyze_single(client, file_ref, ANALYSIS_PROMPT)
    return _dict_to_result(data)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python analyzer.py <video_path> [api_key]")
        print("  api_key defaults to GEMINI_API_KEY env var")
        sys.exit(1)

    video = sys.argv[1]
    key = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("Error: No API key provided. Set GEMINI_API_KEY or pass as 2nd arg.")
        sys.exit(1)

    result = asyncio.run(analyze_video(video, api_key=key))

    print(f"\n{'='*60}")
    print(f"Speakers: {', '.join(result.speakers)}")
    print(f"Summary: {result.summary}")
    print(f"Key moments: {len(result.key_moments)}")
    print(f"Topic segments: {len(result.topic_segments)}")
    print(f"Chapter suggestions: {len(result.chapter_suggestions)}")

    print(f"\n--- Chapters ---")
    for ch in result.chapter_suggestions:
        print(f"  {_seconds_to_hms(ch.start_seconds)}  {ch.title_ko} ({ch.title})")

    print(f"\n--- Top 5 Key Moments ---")
    top5 = sorted(result.key_moments, key=lambda m: m.importance, reverse=True)[:5]
    for km in top5:
        print(f"  [{_seconds_to_hms(km.time_seconds)}] ({km.emotion}, {km.importance}/5)")
        print(f"    {km.speaker}: \"{km.quote}\"")

    print(f"\n--- Transcript (first 500 chars) ---")
    print(result.transcript[:500])
