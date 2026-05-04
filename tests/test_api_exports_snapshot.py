from __future__ import annotations

import importlib
from pathlib import Path

PUBLIC_MODULES = (
    "fastapi_restly",
    "fastapi_restly.db",
    "fastapi_restly.models",
    "fastapi_restly.pytest_fixtures",
    "fastapi_restly.query",
    "fastapi_restly.schemas",
    "fastapi_restly.testing",
    "fastapi_restly.views",
)


def _render_api_exports() -> str:
    lines: list[str] = []
    top_level_exports: set[str] = set()

    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        module_exports = sorted(module.__all__)
        lines.append(f"{module_name}:")
        lines.extend(f"  - {name}" for name in module_exports)
        if module_name == "fastapi_restly":
            top_level_exports = set(module_exports)

    lines.append("")
    lines.append("submodule-only exports:")
    for module_name in PUBLIC_MODULES[1:]:
        module = importlib.import_module(module_name)
        submodule_only_exports = sorted(set(module.__all__) - top_level_exports)
        lines.append(f"{module_name}:")
        lines.extend(f"  - {name}" for name in submodule_only_exports)

    return "\n".join(lines) + "\n"


def test_api_exports_match_snapshot():
    snapshot_path = Path(__file__).with_name("api_exports_snapshot.txt")

    assert _render_api_exports() == snapshot_path.read_text()
