from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def pick_color(percent: float) -> str:
    if percent >= 90:
        return "#4c1"
    if percent >= 75:
        return "#97CA00"
    if percent >= 60:
        return "#dfb317"
    if percent >= 40:
        return "#fe7d37"
    return "#e05d44"


def text_width(text: str) -> int:
    return max(38, 7 * len(text) + 10)


def build_badge(label: str, value: str, color: str) -> str:
    label_width = text_width(label)
    value_width = text_width(value)
    total_width = label_width + value_width
    label_center = label_width / 2
    value_center = label_width + (value_width / 2)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20" role="img" aria-label="{label}: {value}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{total_width}" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{value_width}" height="20" fill="{color}"/>
    <rect width="{total_width}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <text x="{label_center}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{label_center}" y="14">{label}</text>
    <text x="{value_center}" y="15" fill="#010101" fill-opacity=".3">{value}</text>
    <text x="{value_center}" y="14">{value}</text>
  </g>
</svg>
"""


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: render_coverage_badge.py <coverage.json> <badge.svg> <summary.json>",
            file=sys.stderr,
        )
        return 2

    coverage_path = Path(sys.argv[1])
    badge_path = Path(sys.argv[2])
    summary_path = Path(sys.argv[3])

    coverage_data = json.loads(coverage_path.read_text())
    totals = coverage_data["totals"]
    percent = float(totals["percent_covered"])
    display_percent = f"{math.floor(percent + 0.5)}%"

    badge_path.parent.mkdir(parents=True, exist_ok=True)
    badge_path.write_text(
        build_badge("coverage", display_percent, pick_color(percent))
    )

    summary = {
        "label": "coverage",
        "message": display_percent,
        "color": pick_color(percent),
        "schemaVersion": 1,
        "coverage": percent,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
