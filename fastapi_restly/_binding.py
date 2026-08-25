"""FastAPI integration for context binding.

Builds the one artifact every bind-dependency spelling generates: an
async-generator dependency with a synthesized signature, one keyword-only
parameter per bound slot, each fed by a source dependency. Async so the
bind lands in the request's task, where both async and threadpool (def)
endpoints read it; sources are the caller's own dependencies, so
``app.dependency_overrides`` keeps working. Reached through
``ContextParam.depends()`` and ``ContextNamespace.depends()``.
"""

from __future__ import annotations

import inspect
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

from fastapi import Depends
from fastapi.params import Depends as _DependsMarker

from ._contextargs import _origin_override

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
    entries: list[tuple["ContextParam[Any]", Any]], origin: str | None
) -> Any:
    """A Depends whose dependency binds each slot from its source."""
    named: list[tuple[str, ContextParam[Any], Any]] = []
    for param, source in entries:
        fn = param._param_fn
        assert fn is not None and fn.accepted  # invariant: set at declaration
        named.append((next(iter(fn.accepted)), param, source))

    async def _bind(**values: Any):
        with ExitStack() as stack:
            token = _origin_override.set(origin)
            try:
                for name, param, _ in named:
                    stack.enter_context(param.bind(**{name: values[name]}))
            finally:
                _origin_override.reset(token)
            yield

    _bind.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [_source_parameter(name, source) for name, _, source in named]
    )
    _bind.__name__ = "bind_" + "_".join(name for name, _, _ in named)
    return Depends(_bind)
