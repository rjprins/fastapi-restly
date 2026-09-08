"""Declaration-time construction of clauses and context parameters."""

from __future__ import annotations

import functools
import inspect
import sys
from contextlib import ExitStack, contextmanager
from typing import Any, Callable, ClassVar, Iterator, get_args, get_origin

from sqlalchemy import ColumnElement, Select, bindparam

from .._binding import _bind_dependency
from .._contextargs import _caller_origin, contextual
from ._runtime import ContextParam, TransformClause, WhereClause, _embedded_slots

__all__ = ["ContextNamespace", "transform_clause", "where_clause"]


def _context_member_type(cls: type, name: str, annotation: Any) -> Any | None:
    # tolerant per-member resolve, like the parameter markers: a stringified
    # annotation (PEP 563) that names ContextParam is accepted with the type
    # unresolved; anything that is not a ContextParam annotation raises
    if isinstance(annotation, str):
        module = sys.modules.get(cls.__module__)
        try:
            annotation = eval(annotation, getattr(module, "__dict__", {}))  # noqa: S307
        except Exception:
            if "ContextParam" in annotation:
                return None
            raise TypeError(
                f"{cls.__name__}.{name}: a ContextNamespace member is "
                f"annotated `ContextParam[T]`; cannot resolve {annotation!r}"
            ) from None
    if get_origin(annotation) is ClassVar:
        args = get_args(annotation)
        annotation = args[0] if args else annotation
    if annotation is ContextParam:
        return None
    if get_origin(annotation) is ContextParam:
        args = get_args(annotation)
        return args[0] if args else None
    raise TypeError(
        f"{cls.__name__}.{name}: a ContextNamespace member is annotated "
        f"`ContextParam[T]`, got {annotation!r}"
    )


class ContextNamespace:
    """Declares the ContextParams of one context, one per annotation.

    Subclass and annotate: each public annotation `name: ContextParam[T]`
    materializes a ContextParam bound under `name`, so the attribute name
    is the bind name and a typo fails at import. Assigning an existing
    member adopts it (`tenant_id = Current.tenant_id`), so namespaces can
    share one slot; helpers take a leading underscore. The conventional
    app-wide subclass is named Current; a value with a smaller audience
    gets a smaller namespace beside its consumers. Declaring here says
    where a value lives, not where it is bound: binding stays at the
    narrowest level that knows the value, and an unbound read still
    raises.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        annotations = cls.__dict__.get("__annotations__") or {}
        for name, annotation in annotations.items():
            if name.startswith("_"):
                continue
            existing = vars(cls).get(name)
            if isinstance(existing, ContextParam):
                continue
            type_ = _context_member_type(cls, name, annotation)
            setattr(cls, name, _context_param(name, type_))
        for name, value in list(vars(cls).items()):
            if name.startswith("_"):
                continue
            if not isinstance(value, ContextParam):
                raise TypeError(
                    f"{cls.__name__}.{name} is not a ContextParam; declare "
                    "members by annotation (`name: ContextParam[T]`), adopt "
                    "one by assignment, or prefix a helper with an underscore"
                )

    @classmethod
    def _members(cls) -> dict[str, ContextParam[Any]]:
        members: dict[str, ContextParam[Any]] = {}
        for klass in reversed(cls.__mro__):
            for name, value in vars(klass).items():
                if isinstance(value, ContextParam):
                    members[name] = value
        return members

    @classmethod
    @contextmanager
    def bind(cls, /, **values: Any) -> Iterator[None]:
        """Bind member values for the duration of the with block.

        Keys are member names; a name this namespace does not declare
        raises. Equivalent to nesting each member's own bind().
        """
        members = cls._members()
        unknown = sorted(set(values) - set(members))
        if unknown:
            raise TypeError(f"{cls.__name__} has no member(s): " + ", ".join(unknown))
        with ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(members[name].bind(**{name: value}))
            yield

    @classmethod
    def depends(cls, /, **sources: Any) -> Any:
        """A FastAPI dependency that binds the named members per request.

        Keyword names are member names; each value is the dependency the
        member's value comes from (a callable, a ``Depends(...)``, or an
        ``Annotated`` alias), so ``app.dependency_overrides`` keeps
        working. One generated dependency binds them all; see
        :meth:`ContextParam.depends` for the single-slot form.
        """
        members = cls._members()
        unknown = sorted(set(sources) - set(members))
        if unknown:
            raise TypeError(f"{cls.__name__} has no member(s): " + ", ".join(unknown))
        entries = [(members[name], source) for name, source in sources.items()]
        return _bind_dependency(entries, _caller_origin())

    @classmethod
    def explain(cls) -> str:
        """Each member with its bound value and origin, or UNBOUND."""
        lines = [cls.__name__]
        for name, member in sorted(cls._members().items()):
            fn = member._param_fn
            bound = fn.bindings() if fn is not None else {}
            if name in bound:
                value, origin = bound[name]
                try:
                    shown = repr(value)
                except Exception:
                    shown = f"<unrepresentable {type(value).__name__}>"
                if len(shown) > 60:
                    shown = shown[:57] + "..."
                suffix = f"   bound at {origin}" if origin else ""
                lines.append(f"├─ {name} = {shown}{suffix}")
            else:
                lines.append(f"├─ {name}: UNBOUND")
        if len(lines) > 1:
            lines[-1] = "└─" + lines[-1][2:]
        return "\n".join(lines)


def _validate_clause_fn(fn: Callable) -> None:
    label = getattr(fn, "__name__", "clause function")
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(
        getattr(type(fn), "__call__", None)
    ):
        raise TypeError(
            f"{label} is async; clause functions build expressions and must be sync"
        )
    for param in inspect.signature(fn).parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError(
                f"{label} takes *{param.name}; clause function parameters are "
                "bound by name, so variadic parameters cannot be"
            )
        if param.default is not inspect.Parameter.empty:
            raise TypeError(
                f"{label} gives {param.name!r} a default; bound parameters must "
                "not have one; a default would silently stand in when the "
                "binding is missing"
            )


def _marker_slots(fn: Callable) -> tuple[Callable, tuple[ContextParam, ...]]:
    # parameters annotated with a ContextParam (Annotated[T, slot]) are fed
    # from the slot instead of the function's own binding namespace. Each
    # annotation resolves individually and tolerantly: an unresolvable one
    # (TYPE_CHECKING-only names under PEP 563) simply carries no marker,
    # instead of get_type_hints() failing the whole function
    raw = getattr(fn, "__annotations__", {})
    globalns = getattr(fn, "__globals__", {})
    localns: dict[str, object] = {}
    closure = getattr(fn, "__closure__", None)
    if closure:
        for cell_name, cell in zip(fn.__code__.co_freevars, closure):
            try:
                localns[cell_name] = cell.cell_contents
            except ValueError:
                pass
    markers: dict[str, ContextParam] = {}
    for pname in inspect.signature(fn).parameters:
        annotation = raw.get(pname)
        if isinstance(annotation, str):
            source = annotation
            try:
                annotation = eval(annotation, globalns, localns)  # noqa: S307
            except Exception:
                if "Annotated" in source:
                    label = getattr(fn, "__qualname__", fn)
                    raise TypeError(
                        f"cannot resolve the annotation {source!r} on parameter "
                        f"{pname!r} of {label}; an Annotated marker must be "
                        "resolvable at declaration time. Define the ContextParam "
                        "at module scope, or avoid string annotations for this "
                        "function"
                    ) from None
                continue
        for meta in getattr(annotation, "__metadata__", ()):
            if isinstance(meta, ContextParam):
                markers[pname] = meta
    if not markers:
        return fn, ()

    @functools.wraps(fn)
    def filled(*args, **kwargs):
        for pname, slot in markers.items():
            if pname not in kwargs:
                kwargs[pname] = slot()
        return fn(*args, **kwargs)

    sig = inspect.signature(fn)
    filled.__signature__ = sig.replace(  # type: ignore[attr-defined]
        parameters=[p for p in sig.parameters.values() if p.name not in markers]
    )
    return filled, tuple(dict.fromkeys(markers.values()))


def where_clause(
    condition: ColumnElement[bool] | Callable[..., ColumnElement[bool]],
) -> WhereClause:
    """A WhereClause from a condition or a function returning one.

    A ready-made ColumnElement is reused as-is; a ContextParam embedded
    in it is detected and attached, so binding reaches the slot through
    this clause. A function's parameters are filled from values bound
    via Clause.bind() each time the clause resolves; a parameter marked
    Annotated[T, slot] is fed from that slot instead.

    In a ClauseNamespace body, stack this decorator over ``staticmethod``:
    the marker keeps a type checker from reading the def as a method, and
    is unwrapped here.
    """
    if isinstance(condition, staticmethod):
        condition = condition.__func__
    clause = WhereClause()
    if callable(condition):
        _validate_clause_fn(condition)
        fn, slots = _marker_slots(condition)
        clause._where_fn = contextual(fn)
        clause._children = slots
    else:
        clause._where_fn = contextual(lambda: condition)
        clause._children = _embedded_slots(condition)
        clause._condition = condition
    return clause


def transform_clause(fn: Callable[..., Select[Any]]) -> TransformClause:
    """A TransformClause from a function that reshapes a Select.

    The first parameter receives the statement; any further parameters
    are filled from values bound via Clause.bind(). A parameter marked
    Annotated[T, slot] is fed from that ContextParam instead. Stacks over
    ``staticmethod`` in a namespace body, like where_clause().
    """
    if isinstance(fn, staticmethod):
        fn = fn.__func__
    _validate_clause_fn(fn)
    wrapped, slots = _marker_slots(fn)
    clause = TransformClause()
    clause._transform_fn = contextual(wrapped)
    if clause._transform_fn.accepted is not None:
        own_params = list(inspect.signature(wrapped).parameters)
        clause._transform_routable = clause._transform_fn.accepted - set(own_params[:1])
    clause._children = slots
    return clause


def _context_param(name: str, type_: Any | None = None) -> ContextParam[Any]:
    """A ContextParam holding one value, bound under `name`.

    The construction primitive behind ContextNamespace, deliberately not
    public: a slot is pure name and identity, so it is declared where it
    has an address. The optional type lands in the slot's synthesized
    signature, where introspection can read it.
    """

    def fn(**values):
        return values[name]

    fn.__name__ = fn.__qualname__ = f"ContextParam({name})"
    annotation = type_ if type_ is not None else inspect.Parameter.empty
    setattr(
        fn,
        "__signature__",
        inspect.Signature(
            [
                inspect.Parameter(
                    name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation
                )
            ]
        ),
    )
    clause = ContextParam._blank()
    clause._param_fn = contextual(fn)
    placeholder = bindparam(name)
    setattr(placeholder, "_fr_param", clause)
    clause._placeholder = placeholder
    return clause
