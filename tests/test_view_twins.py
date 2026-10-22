"""RestView and AsyncRestView are twins that are kept by hand.

This test maps the async spelling of ``views/_async.py`` to the sync one and
compares the result with ``views/_sync.py``, line by line. A change to one
twin that the other misses fails here, until the other twin gets it too or
the difference goes on the list below with its reason.
"""

import difflib
import re
from pathlib import Path

from fastapi_restly.views import _async, _sync

#: The async names and their sync twins, applied in this order.
_RENAMES = (
    ("AbstractAsyncContextManager", "AbstractContextManager"),
    ("AsyncSessionDep", "SessionDep"),
    ("AsyncSession", "Session"),
    ("AsyncRestView", "RestView"),
    ("_async_shared_write_action_commit", "_shared_write_action_commit"),
    ("_async_bracket", "_bracket"),
    ("async_write_action", "sync_write_action"),
    ("async_make_new_object", "make_new_object"),
    ("async_update_object", "update_object"),
    ("async_delete_object", "delete_object"),
    ("is_async=True", "is_async=False"),
    ("an async CRUD", "a sync CRUD"),
)

#: The differences on purpose: (why, the async lines, the sync lines). The
#: async lines are written as in the async file.
_KNOWN = (
    (
        "the async view awaits what not_found or a filter method returns",
        ["import inspect"],
        [],
    ),
    (
        "the async view sets the _fr_async_view marker",
        ["from typing import Any, ClassVar, cast, final"],
        ["from typing import Any, cast, final"],
    ),
    (
        "the marker that tells the base view that a view is async",
        ["_fr_async_view: ClassVar[bool] = True"],
        [],
    ),
    (
        "not_found may be async on an async view",
        ["if inspect.isawaitable(error):", "error = await error"],
        [],
    ),
    (
        "apply_list_params of the async view must be awaited",
        [
            "Async, as :meth:`apply_filters` is: call it with ``await``. Override",
            "for list params of your own; the common case is driven by",
        ],
        ["Override for list params of your own; the common case is driven by"],
    ),
    (
        "a filter method may be async on an async view",
        [
            "results: list[tuple[str, Any]] = []",
            "for own, value in self._filter_method_values(list_params):",
            "result = getattr(self, own.method)(value)",
            "if inspect.isawaitable(result):",
            "result = await result",
            'results.append((f"{type(self).__name__}.{own.method}", result))',
        ],
        [
            "results = [",
            '(f"{type(self).__name__}.{own.method}", getattr(self, own.method)(value))',
            "for own, value in self._filter_method_values(list_params)",
            "]",
        ],
    ),
)


def _as_sync(text: str) -> str:
    text = re.sub(r"\basync (def|with|for) ", r"\1 ", text)
    text = re.sub(r"\bawait ", "", text)
    for old, new in _RENAMES:
        text = text.replace(old, new)
    return text


def _lines(text: str) -> list[str]:
    """The stripped lines of ``text``. The names of a multi-line import are
    sorted, because a rename changes where ruff sorts a name."""
    lines: list[str] = []
    names: list[str] | None = None
    for line in (line.strip() for line in text.splitlines()):
        if names is None:
            lines.append(line)
            if line.startswith("from ") and line.endswith("import ("):
                names = []
        elif line == ")":
            lines.extend(sorted(names))
            lines.append(line)
            names = None
        else:
            names.append(line)
    return lines


def test_the_sync_and_async_views_do_not_drift():
    async_lines = _lines(_as_sync(Path(_async.__file__).read_text()))
    sync_lines = _lines(Path(_sync.__file__).read_text())
    known = {
        (tuple(_lines(_as_sync("\n".join(a)))), tuple(s)): why for why, a, s in _KNOWN
    }

    found = set()
    unexpected = []
    matcher = difflib.SequenceMatcher(None, async_lines, sync_lines, autojunk=False)
    for tag, a1, a2, s1, s2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        difference = (tuple(async_lines[a1:a2]), tuple(sync_lines[s1:s2]))
        if difference in known:
            found.add(difference)
        else:
            unexpected.append(
                f"_async.py line {a1 + 1}: {list(difference[0])}\n"
                f"_sync.py line {s1 + 1}: {list(difference[1])}"
            )

    assert not unexpected, "The twins differ:\n" + "\n\n".join(unexpected)
    stale = [why for difference, why in known.items() if difference not in found]
    assert not stale, f"These known differences are gone: {stale}"
