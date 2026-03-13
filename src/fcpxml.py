"""Generate Final Cut Pro FCPXML v1.11 marker files.

Produces valid FCPXML that Final Cut Pro 10.6+ can import, with markers
placed on a timeline clip referencing a video asset.

No external dependencies -- uses only xml.etree.ElementTree.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

VALID_COLORS = frozenset({"blue", "green", "red", "purple", "orange"})

# FCPXML 1.11 color keywords used inside <marker> elements.
# FCP recognises these as the "value" for the marker's visual color.
_FCPXML_COLOR_NAMES: dict[str, str] = {
    "blue": "Blue",
    "green": "Green",
    "red": "Red",
    "purple": "Purple",
    "orange": "Orange",
}


@dataclass
class Marker:
    """A single marker on the timeline."""

    time_seconds: float
    title: str
    note: str = ""
    color: str = "blue"

    def __post_init__(self) -> None:
        if self.color not in VALID_COLORS:
            raise ValueError(
                f"Invalid color '{self.color}'. "
                f"Must be one of {sorted(VALID_COLORS)}"
            )


@dataclass
class VideoInfo:
    """Metadata about the source video file."""

    filename: str
    duration_seconds: float
    framerate: float = 23.976


# ---------------------------------------------------------------------------
# Rational-time helpers
# ---------------------------------------------------------------------------

_KNOWN_TIMEBASES: dict[str, tuple[int, int]] = {
    "23.976": (24000, 1001),
    "24":     (24, 1),
    "25":     (25, 1),
    "29.97":  (30000, 1001),
    "30":     (30, 1),
    "50":     (50, 1),
    "59.94":  (60000, 1001),
    "60":     (60, 1),
}


def _framerate_timebase(fps: float) -> tuple[int, int]:
    """Return (timebase_numerator, timebase_denominator) for a given fps.

    For 23.976 fps the timebase is 24000/1001 -- one frame = 1001/24000 s.
    """
    key = f"{fps:.3f}".rstrip("0").rstrip(".")
    if key in _KNOWN_TIMEBASES:
        return _KNOWN_TIMEBASES[key]
    frac = Fraction(fps).limit_denominator(100_000)
    return (frac.numerator, frac.denominator)


def _seconds_to_rational(seconds: float, fps: float) -> str:
    """Convert *seconds* to an FCPXML rational-time string.

    Example: 2.002 s at 23.976 fps -> ``"48048/24000s"``.
    The value is snapped to the nearest whole-frame boundary.
    """
    tb_num, tb_den = _framerate_timebase(fps)
    total_frames = round(seconds * tb_num / tb_den)
    numerator = total_frames * tb_den
    return f"{numerator}/{tb_num}s"


def _frame_duration(fps: float) -> str:
    """Duration of a single frame as a rational-time string."""
    tb_num, tb_den = _framerate_timebase(fps)
    return f"{tb_den}/{tb_num}s"


_FPS_LABELS: dict[str, str] = {
    "23.976": "2398",
    "24":     "24",
    "25":     "25",
    "29.97":  "2997",
    "30":     "30",
    "50":     "50",
    "59.94":  "5994",
    "60":     "60",
}


def _fps_label(fps: float) -> str:
    key = f"{fps:.3f}".rstrip("0").rstrip(".")
    return _FPS_LABELS.get(key, str(int(fps)))


# ---------------------------------------------------------------------------
# FCPXML tree construction
# ---------------------------------------------------------------------------

def _build_fcpxml(video: VideoInfo, markers: list[Marker]) -> ET.Element:
    """Build and return the root ``<fcpxml>`` element."""
    fps = video.framerate
    duration_rat = _seconds_to_rational(video.duration_seconds, fps)
    frame_dur = _frame_duration(fps)
    clip_name = Path(video.filename).stem

    fmt_id = "r1"
    asset_id = "r2"

    # ---- root ----
    root = ET.Element("fcpxml", version="1.11")

    # ---- resources ----
    resources = ET.SubElement(root, "resources")

    ET.SubElement(resources, "format", {
        "id": fmt_id,
        "name": f"FFVideoFormat1080p{_fps_label(fps)}",
        "frameDuration": frame_dur,
        "width": "1920",
        "height": "1080",
    })

    asset = ET.SubElement(resources, "asset", {
        "id": asset_id,
        "name": clip_name,
        "start": "0/1s",
        "duration": duration_rat,
        "hasVideo": "1",
        "hasAudio": "1",
        "format": fmt_id,
    })
    # 절대경로면 file:// URL로, 상대경로면 그대로
    filepath = Path(video.filename)
    if filepath.is_absolute():
        media_src = "file://" + quote(str(filepath))
    else:
        media_src = f"file://./{video.filename}"
    ET.SubElement(asset, "media-rep", {
        "kind": "original-media",
        "src": media_src,
    })

    # ---- library > event > project > sequence > spine > clip ----
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="Markers")
    project = ET.SubElement(event, "project", name=f"{clip_name} Markers")

    sequence = ET.SubElement(project, "sequence", {
        "format": fmt_id,
        "duration": duration_rat,
        "tcStart": "0/1s",
        "tcFormat": "NDF",
    })
    spine = ET.SubElement(sequence, "spine")

    clip = ET.SubElement(spine, "asset-clip", {
        "ref": asset_id,
        "name": clip_name,
        "offset": "0/1s",
        "start": "0/1s",
        "duration": duration_rat,
        "format": fmt_id,
        "tcFormat": "NDF",
    })

    # ---- markers ----
    for m in markers:
        marker_start = _seconds_to_rational(m.time_seconds, fps)
        attrs = {
            "start": marker_start,
            "duration": frame_dur,
            "value": m.title,
        }
        if m.note:
            attrs["note"] = m.note
        ET.SubElement(clip, "marker", attrs)

    return root


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------

def _indent_xml(elem: ET.Element, level: int = 0) -> None:
    """Add whitespace indentation to an ElementTree in-place."""
    pad = "\n" + "    " * level
    child_pad = "\n" + "    " * (level + 1)

    if len(elem):  # has children
        if not elem.text or not elem.text.strip():
            elem.text = child_pad
        for i, child in enumerate(elem):
            _indent_xml(child, level + 1)
        # After last child, closing tag should align with opening tag
        if not child.tail or not child.tail.strip():
            child.tail = pad
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = pad


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_fcpxml(
    video: VideoInfo,
    markers: list[Marker],
    output_path: str,
) -> str:
    """Generate an FCPXML v1.11 file with markers and return the file path.

    Args:
        video: Video file metadata (filename, duration, framerate).
        markers: List of markers to place on the timeline.
        output_path: Destination path for the ``.fcpxml`` file.

    Returns:
        Absolute path of the written file.
    """
    root = _build_fcpxml(video, markers)
    _indent_xml(root)

    tree = ET.ElementTree(root)
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write(b'<!DOCTYPE fcpxml>\n')
        tree.write(fh, encoding="UTF-8", xml_declaration=False)
        fh.write(b'\n')

    return str(out)
