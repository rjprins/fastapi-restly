"""Inject function arguments from context variables.

Decorate a function with @contextual and it gains a .context(...)
context manager plus a .context_call(...) call form. Values set with
.context() are injected as arguments into .context_call() calls made
inside the block, unless the caller passes them explicitly. Calling
the function directly never injects and keeps the original signature,
so plain calls stay fully type checked.

    @contextual
    def greet(name, greeting="Hello"):
        return f"{greeting}, {name}!"

    with greet.context(name="World"):
        greet.context_call()                # "Hello, World!"
        greet.context_call(greeting="Hi")   # "Hi, World!"
        greet("Direct")                     # plain call, no injection

Each function gets its own ContextVar, so values are namespaced per
function and behave correctly across threads and async tasks.

Plain functions only: decorating an instance method is rejected at
decoration time, because obj.method.context_call() could never bind
self (attributes on a function do not participate in method binding).

Two introspection attributes support routing values from the outside:
.accepted is the frozenset of parameter names context() accepts (None
when the function takes **kwargs and accepts anything), and .family is
an opaque token shared by a function and all its aliases.

.alias(name) creates an independent second instance of the function
with its own context namespace. Named aliases are memoized like
logging.getLogger: calling .alias("greet2") anywhere returns the same
instance, so the name is all you need to address it. .alias() without
a name always returns a fresh anonymous instance.
"""

from __future__ import annotations

import inspect
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, ParamSpec, Protocol, TypeVar, cast

__all__ = ["Contextual", "MissingContextValues", "contextual"]


class MissingContextValues(TypeError):
    """context_call() lacked values for required parameters.

    Carries the function label and the missing names so callers can
    raise an error in their own vocabulary.
    """

    def __init__(self, label: str, names: tuple[str, ...]):
        self.label = label
        self.names = names
        super().__init__(
            f"{label}() is missing context values for: " + ", ".join(names)
        )


class _Unset:
    def __repr__(self):
        return "<unset>"


_UNSET = _Unset()

P = ParamSpec("P")
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)


class Contextual(Protocol[P, R_co]):
    # parameter names context() accepts; None means anything (**kwargs)
    accepted: frozenset[str] | None
    # opaque token shared by a function and all its aliases
    family: object

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R_co: ...

    # deliberately loose: calls may omit parameters that come from the context
    def context_call(self, *args: Any, **kwargs: Any) -> R_co: ...

    def context(self, **values: Any) -> AbstractContextManager[None]: ...

    def alias(self, name: str | None = None) -> Contextual[P, R_co]: ...


def contextual(func: Callable[P, R], *, name: str | None = None) -> Contextual[P, R]:
    return _make(func, name, {}, object())


def _make(
    func: Callable[P, R],
    name: str | None,
    registry: dict[str, Contextual[P, R]],
    family: object,
) -> Contextual[P, R]:
    sig = inspect.signature(func)
    params = sig.parameters.values()
    # plain functions only: wrapper is a closure-based function, so its
    # attributes (context_call etc.) can never bind self on obj.method
    first = next(iter(params), None)
    if first is not None and first.name in ("self", "cls"):
        raise TypeError(
            f"{func.__qualname__} looks like a method; contextual supports "
            "plain functions only — obj.method.context_call() cannot bind self"
        )
    accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    allowed = {
        p.name
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    label = name or func.__qualname__
    var: ContextVar[dict[str, Any]] = ContextVar(f"contextual:{label}", default={})
    impl = cast(Callable[..., R], func)

    def merge(args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
        ctx = var.get()
        if not ctx:
            return kwargs
        # arguments the caller provided, positionally or by keyword
        provided = sig.bind_partial(*args, **kwargs).arguments
        merged = dict(kwargs)
        for key, value in ctx.items():
            if key not in provided and key not in merged:
                merged[key] = value
        return merged

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        # direct calls never inject; the signature stays honest
        return func(*args, **kwargs)

    if inspect.iscoroutinefunction(func) and hasattr(inspect, "markcoroutinefunction"):
        # wrapper is sync but returns func's coroutine; keep introspection
        # honest (the marker exists on Python 3.12+ only)
        inspect.markcoroutinefunction(wrapper)

    def context_call(*args: Any, **kwargs: Any) -> R:
        try:
            merged = merge(args, kwargs)
            bound = sig.bind_partial(*args, **merged)
        except TypeError:
            # not a missing-value case; let the natural call error surface
            return impl(*args, **kwargs)
        missing = tuple(
            p.name
            for p in params
            if p.default is inspect.Parameter.empty
            and p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
            and p.name not in bound.arguments
        )
        if missing:
            raise MissingContextValues(label, missing)
        return impl(*args, **merged)

    @contextmanager
    def context(**values: Any):
        if not accepts_any:
            unknown = set(values) - allowed
            if unknown:
                raise TypeError(
                    f"{label}() has no keyword parameter(s): "
                    + ", ".join(sorted(unknown))
                )
        # layer over any enclosing context; reset restores the outer layer
        token = var.set({**var.get(), **values})
        try:
            yield
        finally:
            var.reset(token)

    # introspection only (help(), inspect.signature): the parent's keyword
    # parameters, all keyword-only and optional — any subset may be given,
    # the rest can come from other context() calls or the call itself
    context_params = [
        inspect.Parameter(
            p.name,
            inspect.Parameter.KEYWORD_ONLY,
            default=_UNSET,
            annotation=p.annotation,
        )
        for p in params
        if p.name in allowed
    ]
    if accepts_any:
        var_kw = next(p for p in params if p.kind is inspect.Parameter.VAR_KEYWORD)
        context_params.append(
            inspect.Parameter(var_kw.name, inspect.Parameter.VAR_KEYWORD)
        )
    setattr(context, "__signature__", inspect.Signature(context_params))

    def alias(name: str | None = None) -> Contextual[P, R]:
        # each instance gets its own context namespace; named ones are
        # memoized getLogger-style, so the same name addresses the same
        # instance anywhere in the function's family
        f = cast(Callable[P, R], func)
        if name is None:
            return _make(f, None, registry, family)
        # setdefault is atomic under the GIL: concurrent callers may both
        # build, but every caller gets the one stored instance
        return registry.setdefault(name, _make(f, name, registry, family))

    setattr(wrapper, "context_call", context_call)
    setattr(wrapper, "context", context)
    setattr(wrapper, "alias", alias)
    setattr(wrapper, "accepted", None if accepts_any else frozenset(allowed))
    setattr(wrapper, "family", family)
    return cast("Contextual[P, R]", wrapper)
