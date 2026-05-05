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
    loaded_modules = {
        module_name: importlib.import_module(module_name)
        for module_name in PUBLIC_MODULES
    }
    lines: list[str] = []
    top_level_exports: set[str] = set()

    for module_name in PUBLIC_MODULES:
        module = loaded_modules[module_name]
        module_exports = sorted(
            name
            for name in vars(module)
            if name.isidentifier() and not name.startswith("_")
        )
        lines.append(f"{module_name}:")
        lines.extend(f"  - {name}" for name in module_exports)
        if module_name == "fastapi_restly":
            top_level_exports = set(module_exports)

    lines.append("")
    lines.append("submodule-only exports:")
    for module_name in PUBLIC_MODULES[1:]:
        module = loaded_modules[module_name]
        module_exports = {
            name
            for name in vars(module)
            if name.isidentifier() and not name.startswith("_")
        }
        submodule_only_exports = sorted(module_exports - top_level_exports)
        lines.append(f"{module_name}:")
        lines.extend(f"  - {name}" for name in submodule_only_exports)

    return "\n".join(lines) + "\n"


def test_api_exports_match_snapshot():
    snapshot_path = Path(__file__).with_name("api_exports_snapshot.txt")

    assert _render_api_exports() == snapshot_path.read_text()
