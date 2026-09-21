"""FastAPI integration for context binding.

Builds the one artifact every bind-dependency spelling generates: an
async-generator dependency with a synthesized signature, one keyword-only
parameter per bound member, each fed by a source dependency. Async so the
bind lands in the request's task, where both async and threadpool (def)
endpoints read it; sources are the caller's own dependencies, so
``app.dependency_overrides`` keeps working. Reached through
``ContextNamespace.depends()``.
"""

from __future__ import annotations

import inspect
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

from fastapi import Depends
from fastapi.params import Depends as _DependsMarker

if TYPE_CHECKING:
    from .clauses import ContextParam


def _source_parameter(name: str, source: Any) -> inspect.Parameter:
    """One synthesized dependency parameter for a bind source.

    A source is a dependency in any of its spellings: a callable, a
    ``Depends(...)``, or an ``Annotated[T, Depends(...)]`` alias.
    """
    if isinstance(source, _DependsMarker):
        return inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=source)
    if getattr(source, "__metadata__", None) is not None:
        return inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY, annotation=source
        )
    if callable(source):
        return inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY, default=Depends(source)
        )
    raise TypeError(
        f"a bind source is a dependency: a callable, Depends(...), or an "
        f"Annotated alias; got {type(source).__name__} for {name!r}"
    )


def _bind_dependency(
    entries: list[tuple[str, "ContextParam[Any]", Any]], origin: str | None
) -> Any:
    """A Depends whose dependency binds each member from its source.

    ``origin`` is the ``depends()`` call site: the bind happens inside the
    package, where a frame walk would name the machinery.
    """

    async def _bind(**values: Any):
        with ExitStack() as stack:
            for name, param, _ in entries:
                stack.enter_context(param._bind(values[name], origin))
            yield

    _bind.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [_source_parameter(name, source) for name, _, source in entries]
    )
    _bind.__name__ = "bind_" + "_".join(name for name, _, _ in entries)
    return Depends(_bind)
