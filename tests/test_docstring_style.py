"""Sphinx runs without napoleon here, so a Google-style ``Args:`` block is not
parsed into a parameter table -- it renders as one run-on paragraph in the API
reference. Nitpicky mode cannot catch it either, because the text is valid; it
is only unstyled. This guard is what catches it.

Write parameters as reST field lists instead: ``:param x:``, ``:returns:``,
``:raises SomeError:``.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "fastapi_restly"

# Sections carrying structured data that a field list renders as a table and a
# bare docstring flattens into prose. Prose leads such as "Note:" are absent on
# purpose: they read the same either way.
GOOGLE_SECTIONS = frozenset(
    {
        "Args:",
        "Arguments:",
        "Attributes:",
        "Keyword Args:",
        "Keyword Arguments:",
        "Other Parameters:",
        "Parameters:",
        "Raises:",
        "Returns:",
        "Warns:",
        "Yields:",
    }
)

_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def find_google_style_sections(source: str, filename: str = "<test>") -> list[str]:
    """Report every Google-style section header in ``source``'s docstrings.

    :param source: Python source text to scan.
    :param filename: Name used in the reported locations.
    :returns: One ``file:line: Section:`` string per offending header.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename)):
        if not isinstance(node, _DOCSTRING_OWNERS):
            continue
        docstring = ast.get_docstring(node, clean=False)
        if not docstring:
            continue
        # The docstring is the first statement, so its literal anchors the
        # offsets of the lines inside it.
        first_line = node.body[0].value.lineno  # type: ignore[attr-defined]
        for offset, line in enumerate(docstring.splitlines()):
            if line.strip() in GOOGLE_SECTIONS:
                found.append(f"{filename}:{first_line + offset}: {line.strip()}")
    return found


def test_package_docstrings_use_rest_field_lists():
    offenders: list[str] = []
    scanned = 0
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        scanned += 1
        relative = path.relative_to(PACKAGE_ROOT.parent).as_posix()
        offenders += find_google_style_sections(path.read_text(), relative)

    # A rename that moves the package would otherwise make this pass vacuously.
    assert scanned > 0, f"no Python files found under {PACKAGE_ROOT}"

    assert not offenders, (
        "Google-style docstring sections do not render without napoleon; use "
        "reST field lists (:param x:, :returns:, :raises E:) instead:\n  "
        + "\n  ".join(offenders)
    )


def test_finder_reports_google_style_sections():
    source = '''
def f(a, b):
    """Do a thing.

    Args:
        a: The first.
        b: The second.

    Returns:
        A thing.
    """
'''
    assert find_google_style_sections(source, "m.py") == [
        "m.py:5: Args:",
        "m.py:9: Returns:",
    ]


def test_finder_passes_field_lists_and_reST_literal_blocks():
    source = '''
def f(a):
    """Do a thing.

    Example::

        f(1)

    Note: the value is not validated.

    :param a: The first.
    :returns: A thing.
    """
'''
    assert find_google_style_sections(source, "m.py") == []


def test_finder_scans_module_and_class_docstrings():
    source = '''
"""Module summary.

Attributes:
    x: A module constant.
"""


class C:
    """Class summary.

    Args:
        y: A constructor argument.
    """
'''
    assert find_google_style_sections(source, "m.py") == [
        "m.py:4: Attributes:",
        "m.py:12: Args:",
    ]
