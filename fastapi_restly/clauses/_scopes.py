"""Clause namespaces and model default-scope registration."""

from __future__ import annotations

import weakref
from typing import Any, ClassVar

from sqlalchemy.orm import DeclarativeBase

from ._runtime import UNSCOPED, Unscoped, WhereClause

__all__ = ["ClauseNamespace"]


_NAMESPACES: weakref.WeakKeyDictionary[type[DeclarativeBase], type[ClauseNamespace]] = (
    weakref.WeakKeyDictionary()
)


class ClauseNamespace:
    """Groups clauses under one named class.

    Subclass and put the clauses in the class body; earlier names are
    available to later compositions. On definition the namespace
    validates that every public attribute is a clause (catching a bare
    SQLAlchemy expression that forgot its where_clause() wrapper). Usage
    is by class name (`ItemClauses.visible`): plain attribute access
    that any type checker follows. A clause needs no model; a namespace
    that declares `model = <mapped class>` is *the* namespace for that
    model and registers itself, so its `default_scope` reaches the
    model's reads and reference checks; the model class itself is never
    touched. Define such a namespace in the model's module, so importing
    the model guarantees the registration ran. A namespace without a
    model is a plain group: shared clauses, or a base class whose
    `__init_subclass__` shapes the namespaces that extend it.

    The name `default_scope` is reserved: a clause under that name is
    the scope every view read and every reference check on the model
    applies unless a view declares its own (see the Scopes guide). A
    join-dependent predicate is an EXISTS (.any()/.has()).
    A model subclass inherits the nearest declared `default_scope`
    along its MRO: a namespace that does not declare one leaves an
    inherited scope in force, and `default_scope = UNSCOPED` is the
    explicit opt-out. `None` says nothing here and is rejected.
    """

    model: ClassVar[type[DeclarativeBase]]
    default_scope: ClassVar[WhereClause | Unscoped] = UNSCOPED

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        model = cls.__dict__.get("model")
        for name, value in vars(cls).items():
            if name.startswith("_") or name == "model":
                continue
            if name == "default_scope":
                if value is UNSCOPED:
                    continue
                if value is None:
                    raise TypeError(
                        f"{cls.__name__}.default_scope = None says nothing; "
                        "opt out explicitly with UNSCOPED "
                        "(fr.clauses.UNSCOPED)"
                    )
            if not isinstance(value, WhereClause):
                if name == "default_scope":
                    raise TypeError(
                        f"{cls.__name__}.default_scope must be a WhereClause or "
                        "UNSCOPED; wrap a raw expression with where_clause()"
                    )
                raise TypeError(
                    f"{cls.__name__}.{name} is not a clause; wrap it with "
                    "where_clause(), or prefix it with an underscore if it "
                    "is a helper"
                )
        if model is None:
            return
        existing = _NAMESPACES.get(model)
        if existing is not None:
            raise TypeError(
                f"{model.__name__} already has a clause namespace: {existing.__name__}"
            )
        _NAMESPACES[model] = cls


_NO_DECLARATION = object()


def _declared_default_scope(namespace: type[ClauseNamespace]) -> Any:
    """The namespace's own `default_scope` declaration, or _NO_DECLARATION.

    Looks above the ClauseNamespace base, so a namespace subclass keeps
    its parent's declaration while the base's UNSCOPED class default
    does not count as one.
    """
    for klass in namespace.__mro__:
        if klass is ClauseNamespace:
            return _NO_DECLARATION
        if "default_scope" in vars(klass):
            return vars(klass)["default_scope"]
    return _NO_DECLARATION


def _default_scope(model: type[DeclarativeBase]) -> WhereClause | None:
    """The model's `default_scope`, or None for unscoped.

    Walks the model's MRO and applies the first registered namespace
    that declares `default_scope` itself: a model subclass inherits the
    base model's scope through namespaces that stay silent, and
    UNSCOPED stops the walk as the explicit opt-out. A `default_scope`
    that is not a WhereClause raises: the namespace validation
    guarantees it at class definition, so this only fires on a later
    assignment, which must not silently weaken the model's scope.
    """
    for klass in model.__mro__:
        namespace = _NAMESPACES.get(klass)
        if namespace is None:
            continue
        scope = _declared_default_scope(namespace)
        if scope is _NO_DECLARATION:
            continue
        if scope is UNSCOPED:
            return None
        if not isinstance(scope, WhereClause):
            raise TypeError(
                f"{namespace.__name__}.default_scope must be a WhereClause or "
                "UNSCOPED; wrap a raw expression with where_clause()"
            )
        return scope
    return None
