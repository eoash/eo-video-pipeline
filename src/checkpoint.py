"""Checkpoint manager for pipeline stages.

Saves intermediate results after each stage so that a failed run
can be resumed from the last successful stage instead of starting over.

Checkpoint file: output/<video_name>_checkpoint.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Checkpoint:
    """Manages per-video pipeline checkpoints.

    Stages (in order):
      1. audio_extracted  — path to extracted audio file
      2. analysis         — full AnalysisResult as dict
      3. sheets           — Google Sheets URL
      4. fcpxml           — FCPXML file path
      5. slack            — notification sent (bool)
    """

    STAGES = ["audio_extracted", "analysis", "sheets", "fcpxml", "slack"]

    def __init__(self, video_name: str, output_dir: str = "./output"):
        self.video_name = video_name
        self.path = Path(output_dir) / f"{video_name}_checkpoint.json"
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            logger.info("Loaded checkpoint: %s (stages: %s)", self.path.name,
                        ", ".join(self.completed_stages()))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def completed_stages(self) -> list[str]:
        return [s for s in self.STAGES if s in self._data]

    def is_done(self, stage: str) -> bool:
        return stage in self._data

    def get(self, stage: str) -> Any:
        return self._data.get(stage)

    def save_stage(self, stage: str, value: Any) -> None:
        self._data[stage] = value
        self._data["_updated"] = datetime.now(timezone.utc).isoformat()
        self._save()
        logger.info("Checkpoint saved: %s.%s", self.video_name, stage)

    def next_stage(self) -> str | None:
        """Return the first incomplete stage, or None if all done."""
        for stage in self.STAGES:
            if stage not in self._data:
                return stage
        return None

    def is_complete(self) -> bool:
        return all(s in self._data for s in self.STAGES)

    def clear(self) -> None:
        """Remove checkpoint file to force full re-run."""
        if self.path.exists():
            self.path.unlink()
            logger.info("Checkpoint cleared: %s", self.path.name)
        self._data = {}
