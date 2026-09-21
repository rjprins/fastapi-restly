"""Context members: values bound around a unit of work and read anywhere."""

from __future__ import annotations

import contextlib
import os
import sys
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from typing import (
    Any,
    ClassVar,
    Generic,
    Iterator,
    NoReturn,
    TypeVar,
    get_args,
    get_origin,
)

from sqlalchemy import bindparam
from sqlalchemy.sql.expression import BindParameter

__all__ = ["ContextNamespace", "ContextParam"]

_T = TypeVar("_T")


class ContextNamespace:
    """Declares the ContextParams of one context, one per annotation.

    Subclass and annotate: each public annotation `name: ContextParam[T]`
    materializes a ContextParam bound under `name`, so the attribute name
    is the bind name and a typo fails at import. Assigning an existing
    member adopts it (`tenant_id = Current.tenant_id`), so namespaces can
    share one member; helpers take a leading underscore. The conventional
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
            setattr(cls, name, ContextParam._declare(name, cls.__name__, type_))
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
    def _named(cls, names: Any) -> dict[str, ContextParam[Any]]:
        members = cls._members()
        unknown = sorted(set(names) - set(members))
        if unknown:
            raise TypeError(f"{cls.__name__} has no member(s): " + ", ".join(unknown))
        return {name: members[name] for name in names}

    @classmethod
    @contextmanager
    def bind(cls, /, **values: Any) -> Iterator[None]:
        """Bind member values for the duration of the with block.

        Keys are member names; a name this namespace does not declare
        raises. An inner bind replaces the value until its block exits.
        """
        members = cls._named(values)
        origin = _caller_origin()
        with ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(members[name]._bind(value, origin))
            yield

    @classmethod
    def depends(cls, /, **sources: Any) -> Any:
        """A FastAPI dependency that binds the named members per request.

        Keyword names are member names; each value is the dependency the
        member's value comes from (a callable, a ``Depends(...)``, or an
        ``Annotated`` alias), so ``app.dependency_overrides`` keeps
        working. The result drops into a ``dependencies=[...]`` list at
        app, router, or view level. It is an async dependency underneath:
        the bind lands in the request task, where async and def endpoints
        alike read it.
        """
        # FastAPI is imported only by an app that binds per request
        from .._binding import _bind_dependency

        members = cls._named(sources)
        entries = [(name, members[name], source) for name, source in sources.items()]
        return _bind_dependency(entries, _caller_origin())

    @classmethod
    def explain(cls) -> str:
        """Each member with its bound value and origin, or UNBOUND."""
        lines = [cls.__name__]
        for name, member in sorted(cls._members().items()):
            bound = member._var.get()
            if bound is None:
                lines.append(f"├─ {name}: UNBOUND")
                continue
            value, origin = bound
            try:
                shown = repr(value)
            except Exception:
                shown = f"<unrepresentable {type(value).__name__}>"
            if len(shown) > 60:
                shown = shown[:57] + "..."
            suffix = f"   bound at {origin}" if origin else ""
            lines.append(f"├─ {name} = {shown}{suffix}")
        if len(lines) > 1:
            lines[-1] = "└─" + lines[-1][2:]
        return "\n".join(lines)


class ContextParam(Generic[_T]):
    """One value of a context: bound around a unit of work, read anywhere.

    Declared in a ContextNamespace (``tenant_id: ContextParam[UUID]``),
    never constructed directly: the namespace is its address, and it is
    bound through the namespace's bind() or depends(). One member, two
    positions. Called, it returns the bound value, or raises LookupError
    when nothing is bound. Embedded in a SQL expression
    (``Item.tenant_id == Current.tenant_id``) it becomes a placeholder,
    filled with the bound value each time a clause that carries it is
    applied.

    A member never stands in for its value: ``==``, ``!=`` and a truth
    test raise. Call the member to read it, and in a SQL expression put
    the column first (``Item.role == Current.role``).
    """

    _name: str
    _owner: str
    _type: Any | None
    # the bound (value, origin), or None: a bound value may itself be None
    _var: ContextVar[tuple[Any, str | None] | None]
    _placeholder: BindParameter[Any]

    def __init__(self) -> None:
        raise TypeError(
            "a ContextParam is not constructed directly; declare it in a "
            "ContextNamespace: `name: ContextParam[T]` in the class body"
        )

    @classmethod
    def _declare(cls, name: str, owner: str, type_: Any | None) -> ContextParam[Any]:
        # the construction path around the teaching __init__
        self = cls.__new__(cls)
        self._name = name
        self._owner = owner
        self._type = type_
        self._var = ContextVar(f"{owner}.{name}", default=None)
        # unique: every use gets its own key, so two members that share a
        # name, or a hand-written bindparam(name), cannot collide
        placeholder = bindparam(name, unique=True)
        # the clause layer finds the member behind a placeholder by this tag
        setattr(placeholder, "_fr_param", self)
        self._placeholder = placeholder
        return self

    # no parameters: SQLAlchemy inspects a column default's signature, and
    # insert_default=Current.user_id depends on this one
    def __call__(self) -> _T:
        """The bound value; LookupError when nothing is bound."""
        bound = self._var.get()
        if bound is None:
            label = f"{self._owner}.{self._name}"
            raise LookupError(
                f"{label} is not bound; bind it around this code with "
                f"{self._owner}.bind({self._name}=...), or per request with "
                f"{self._owner}.depends({self._name}=...)"
            )
        return bound[0]

    @contextmanager
    def _bind(self, value: Any, origin: str | None) -> Iterator[None]:
        token = self._var.set((value, origin))
        try:
            yield
        finally:
            self._var.reset(token)

    def __clause_element__(self) -> BindParameter[Any]:
        # SQLAlchemy's coercion protocol: in an expression the member is a
        # tagged placeholder, filled when a clause that carries it resolves
        return self._placeholder

    # object equality answers False / True whatever is bound, and
    # SQLAlchemy renders that bool as WHERE false / WHERE true
    def __eq__(self, other: object) -> NoReturn:
        raise self._not_its_value("==")

    def __ne__(self, other: object) -> NoReturn:
        raise self._not_its_value("!=")

    # defining __eq__ drops the inherited hash; identity is the right one
    __hash__ = object.__hash__

    # `if Current.is_admin:` would otherwise always pass
    def __bool__(self) -> NoReturn:
        name = self._name
        raise TypeError(
            f"the context member {name!r} is not its value, so it has no "
            f"truth value; read the value by calling the member: {name}()"
        )

    def _not_its_value(self, op: str) -> TypeError:
        name = self._name
        return TypeError(
            f"the context member {name!r} is not its value, so {op} cannot "
            f"compare it; read the value by calling the member ({name}() {op} "
            f"...), or in a SQL expression put the column first "
            f"(Model.column {op} <member>)"
        )

    def __repr__(self) -> str:
        label = f"{self._owner}.{self._name}"
        bound = self._var.get()
        if bound is None:
            return f"<ContextParam {label}>"
        origin = bound[1]
        return (
            f"<ContextParam {label}, bound at {origin}>"
            if origin
            else (f"<ContextParam {label}, bound>")
        )


def _context_member_type(cls: type, name: str, annotation: Any) -> Any | None:
    # tolerant per-member resolve: a stringified annotation (PEP 563) that
    # names ContextParam is accepted with the type unresolved; anything that
    # is not a ContextParam annotation raises
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


_PACKAGE_DIR = os.path.dirname(os.path.dirname(__file__))
_CONTEXTLIB_FILE = contextlib.__file__


def _caller_origin() -> str | None:
    """file:line (function) of the first frame outside this package.

    Walks past the contextmanager plumbing so the origin names the user's
    bind site, not the machinery. Only strings are kept; no frame
    reference survives.
    """
    frame = sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if (
            not filename.startswith(_PACKAGE_DIR + os.sep)
            and filename != _CONTEXTLIB_FILE
        ):
            try:
                path = os.path.relpath(filename)
            except ValueError:
                path = filename
            if path.startswith(".."):
                path = filename
            return f"{path}:{frame.f_lineno} ({frame.f_code.co_name})"
        frame = frame.f_back
    return None
