"""Assert ``example-projects/starter`` is byte-for-byte what ``restly new`` emits.

The snapshot exists so that a template change shows up in review as a concrete
diff of what a user will actually receive, rather than as a change to a file
nobody reads as output. This script is what keeps the two from drifting.

Run it through ``make scaffold-check``.
"""

from __future__ import annotations

import filecmp
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "example-projects" / "starter"

sys.path.insert(0, str(REPO))

from fastapi_restly._scaffold._generate import Options, generate  # noqa: E402

# The snapshot is the default combination, which is also the shape the
# documentation prescribes.
OPTIONS = Options(name="myapp")


# Tool byproducts, not generator output. Running the repo's own ruff or pytest
# over the snapshot leaves these behind.
IGNORED_DIRECTORIES = frozenset(
    {"__pycache__", ".ruff_cache", ".pytest_cache", ".venv", ".mypy_cache"}
)


def _relative_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and IGNORED_DIRECTORIES.isdisjoint(path.parts)
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as work:
        expected_root = Path(work) / "starter"
        generate(OPTIONS, expected_root)

        expected = _relative_files(expected_root)
        actual = _relative_files(SNAPSHOT)

        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        differing = sorted(
            path
            for path in expected & actual
            if not filecmp.cmp(expected_root / path, SNAPSHOT / path, shallow=False)
        )

        if not (missing or extra or differing):
            print(f"starter matches the templates ({len(expected)} files)")
            return 0

        print("example-projects/starter has drifted from the scaffold templates.\n")
        for path in missing:
            print(f"  missing:   {path}")
        for path in extra:
            print(f"  unexpected: {path}")
        for path in differing:
            print(f"  differs:   {path}")
        print("\nRegenerate it with:")
        print("  rm -r example-projects/starter")
        print(
            '  uv run python -c "from pathlib import Path; '
            "from fastapi_restly._scaffold._generate import Options, generate; "
            "generate(Options(name='myapp'), Path('example-projects/starter'))\""
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
