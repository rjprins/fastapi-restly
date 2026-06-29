from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

# Stable, version-independent URL every build's switcher reads from, so frozen
# snapshots pick up newly published versions without a rebuild.
BASE_URL = "https://www.fastapi-restly.org/"
# Versioning starts at 0.7; earlier releases predate the snapshot pipeline.
DEFAULT_FLOOR = "0.7"

_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _git_tags() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "tag"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return result.stdout.split()


def published_minors(tags: list[str], floor: tuple[int, int]) -> list[tuple[int, int]]:
    """Minor versions at or above the floor, newest first."""
    minors = set()
    for tag in tags:
        match = _TAG_RE.match(tag.strip())
        if not match:
            continue
        major, minor = int(match.group(1)), int(match.group(2))
        if (major, minor) >= floor:
            minors.add((major, minor))
    return sorted(minors, reverse=True)


def build_entries(minors: list[tuple[int, int]], base_url: str) -> list[dict]:
    base = base_url.rstrip("/") + "/"
    # "latest" is the live build at the site root and the preferred target, so
    # snapshots show a "newer version available" banner.
    entries = [
        {"name": "latest", "version": "latest", "url": base, "preferred": True}
    ]
    for major, minor in minors:
        label = f"{major}.{minor}"
        entries.append({"name": label, "version": label, "url": f"{base}{label}/"})
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the docs version switcher JSON."
    )
    parser.add_argument("output", type=Path, help="path to write switcher.json")
    parser.add_argument("--base", default=BASE_URL, help="site root URL")
    parser.add_argument("--floor", default=DEFAULT_FLOOR, help="oldest versioned minor")
    args = parser.parse_args(argv)

    parts = [int(part) for part in args.floor.split(".")]
    floor = (parts[0], parts[1] if len(parts) > 1 else 0)
    entries = build_entries(published_minors(_git_tags(), floor), args.base)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(entries, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
