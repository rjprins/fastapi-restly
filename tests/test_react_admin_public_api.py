"""The react-admin views are a user of Restly: they use the public API, as a
view with list params of its own would. A private name that they import has
no public form yet. The test lists those names, so that a new one is a choice
and not an accident."""

import ast
from pathlib import Path

import fastapi_restly.views._react_admin as react_admin

#: The private names that the react-admin views still import: the helpers of
#: the view settings, which have no public form yet.
PRIVATE_IMPORTS = {
    "._base._how_set",
    "._base._list_filter_methods",
    "._base._list_setting",
    "._base._owner",
    "._base._set_by",
}


def _is_private(dotted: str) -> bool:
    """A name with a part that starts with one ``_``, such as ``_base``."""
    return any(
        part.startswith("_") and not part.startswith("__") for part in dotted.split(".")
    )


def test_the_react_admin_views_import_only_public_names_and_the_listed_ones():
    tree = ast.parse(Path(react_admin.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imported.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert {name for name in imported if _is_private(name)} == PRIVATE_IMPORTS
