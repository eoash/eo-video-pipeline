"""Slack notification for completed video analysis.

Sends a formatted message to a Slack channel via Incoming Webhook
when a video analysis pipeline run finishes.

Requires:
  SLACK_WEBHOOK_URL in .env
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _seconds_to_mmss(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def send_analysis_complete(
    webhook_url: str,
    video_name: str,
    summary: str,
    speakers: list[str],
    key_moments: list[dict],
    chapters: list[dict],
    fcpxml_path: str,
    sheet_url: str | None = None,
) -> bool:
    """Send a Slack notification with analysis results.

    Parameters
    ----------
    webhook_url:
        Slack Incoming Webhook URL.
    video_name:
        Name of the analyzed video file.
    summary:
        Analysis summary text.
    speakers:
        List of detected speaker names.
    key_moments:
        Top key moments (dicts with time_seconds, speaker, quote, importance).
    chapters:
        Chapter suggestions (dicts with start_seconds, title_ko).
    fcpxml_path:
        Path to the generated FCPXML file.
    sheet_url:
        Optional Google Sheets URL.

    Returns
    -------
    bool
        True if the message was sent successfully.
    """
    # Top 5 key moments
    top5 = sorted(key_moments, key=lambda m: m.get("importance", 0), reverse=True)[:5]
    moments_text = "\n".join(
        f"  `{_seconds_to_mmss(m['time_seconds'])}` ({m['speaker']}) _{m['quote'][:80]}_"
        for m in top5
    )

    # Chapters
    chapters_text = "\n".join(
        f"  `{_seconds_to_mmss(c['start_seconds'])}` {c['title_ko']}"
        for c in chapters
    )

    # Build blocks
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🎬 분석 완료: {video_name}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*화자:* {', '.join(speakers)}\n\n{summary}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🔥 핵심 발언 TOP 5*\n{moments_text}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📌 챕터 제안*\n{chapters_text}"},
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"📁 FCPXML: `{fcpxml_path}`"},
            ],
        },
    ]

    if sheet_url:
        blocks[-1]["elements"].append(
            {"type": "mrkdwn", "text": f"📊 <{sheet_url}|Google Sheets>"}
        )

    payload = json.dumps({"blocks": blocks}).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Slack notification sent for %s", video_name)
                return True
            logger.warning("Slack webhook returned status %d", resp.status)
            return False
    except urllib.error.URLError as e:
        logger.error("Failed to send Slack notification: %s", e)
        return False
