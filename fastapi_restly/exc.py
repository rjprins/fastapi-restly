"""Public exception hierarchy for FastAPI-Restly.

Two families:

* Configuration-time errors (:class:`RestlyError` / :class:`RestlyConfigurationError`)
  raised when the framework is misused before/at setup.
* Request-time HTTP errors (:class:`NotFound` / :class:`Forbidden` /
  :class:`Conflict` / :class:`BadQueryParam`) raised while handling a request.
  These subclass :class:`fastapi.HTTPException`, so the default responses are
  identical to raising ``HTTPException`` directly -- but a user can
  ``app.add_exception_handler(fr.exc.NotFound, ...)`` to reshape Restly's errors
  distinctly (e.g. into RFC 9457 problem+json).
"""

import fastapi


class RestlyError(Exception):
    """Base class of Restly's configuration errors.

    The request-time HTTP errors, such as :class:`NotFound`, do not derive
    from it. They subclass :class:`RestlyHTTPError`, a
    :class:`fastapi.HTTPException`.
    """


class RestlyConfigurationError(RestlyError):
    """Raised when a setup cannot work: a view, a model, a schema or the test
    setup that is declared wrongly, or Restly used before it is configured.
    """


class RestlyHTTPError(fastapi.HTTPException):
    """Base for Restly's request-time HTTP errors. Subclasses set a status."""

    #: The HTTP status of the response. Each error class sets its own.
    status_code: int = 500
    #: The ``detail`` of the response when the error is raised without a
    #: detail. Each error class sets its own.
    default_detail: str = "Error"

    def __init__(self, detail: object | None = None, **kwargs: object) -> None:
        super().__init__(
            status_code=self.status_code,
            detail=detail if detail is not None else self.default_detail,
            **kwargs,  # type: ignore[arg-type]
        )


class NotFound(RestlyHTTPError):
    """HTTP 404 -- the requested resource does not exist (or is not visible)."""

    status_code = 404
    default_detail = "Not found"


class Forbidden(RestlyHTTPError):
    """HTTP 403 -- the request is not authorized."""

    status_code = 403
    default_detail = "Forbidden"


class Conflict(RestlyHTTPError):
    """HTTP 409 -- the request conflicts with the current resource state.

    Restly raises it for a database conflict, such as a duplicate unique
    value, in a CRUD write or a ``write_action`` block. The SQLAlchemy
    ``IntegrityError`` is its ``__cause__``.
    """

    status_code = 409
    default_detail = "Conflict"


class BadQueryParam(RestlyHTTPError):
    """HTTP 400 -- a list query parameter with a known key, whose value
    Restly cannot apply.

    An example is a filter value that fails the validation of its field,
    such as ``?age=old`` on an ``int`` field. On a list route, a query
    string that does not match the list params, such as an unknown key or a
    bad page, is a 422 instead: a ``RequestValidationError``, in the shape
    that FastAPI uses for a bad parameter.
    """

    status_code = 400
    default_detail = "Invalid query parameter"


class RestlyUncommittedChangesWarning(UserWarning):
    """Emitted when a request finishes with uncommitted changes in the session.

    The write handlers own the commit, so a custom write route that flushes
    (e.g. with ``session.flush()``) but never commits has its changes silently
    rolled back when the session closes. The fix is almost always to commit:
    bracket the mutation with ``write_action(...)`` (the framework then runs
    the commit), or reuse a ``handle_<verb>`` handler. A route that
    *intentionally* rolls back (a validate-then-rollback dry run) can suppress
    the warning for just that request with
    ``session.info["_fr_suppress_uncommitted"] = True``.
    """


class RestlyExpireOnCommitWarning(UserWarning):
    """Emitted when a view writes through a session with
    ``expire_on_commit=True``, once for each view.

    The commit expires the loaded attributes of the saved row. So
    ``snapshot(new)`` in ``after_action_commit`` is empty, and the response
    loads the row from the database again. In async code, that raises
    ``MissingGreenlet``. Restly's own session factories set
    ``expire_on_commit=False``. A sync project that keeps
    ``expire_on_commit=True`` on purpose can filter this warning by its class.
    """


class RestlyMisuseWarning(UserWarning):
    """Emitted at view registration for common framework-misuse patterns.

    Opt-in: enable with ``fr.configure(warn_on_misuse=True)``. When a view
    class is registered via ``include_view``, the framework then flags the
    dominant misuses -- overriding an endpoint method (``<verb>_endpoint``) where
    a business-method override was meant, calling ``session.commit()`` directly in a
    view method, typing a scalar foreign-key column (``post_id``) as an
    ``IDRef`` / ``IDSchema`` reference instead of ``fr.MustExist[int, Model]``,
    and a writable field that sets a column that the model sets
    (``init=False`` with a default) instead of ``fr.ReadOnly``. It also flags
    a view that is registered twice on the same app or router: the second
    ``include_view`` call does nothing.
    """


class RestlySkippedFilterMethodsWarning(UserWarning):
    """Emitted when a view filters a list with list params that do not run
    its filter methods.

    A view runs the methods from :func:`~fastapi_restly.views.list_filter`
    only for its own list params: those of a list route, or a dict or a
    ``QueryParams`` that a method gets. A Pydantic model of another class,
    such as one of your own, does not run them. Then the list can have rows
    that a method would leave out. Pass the view's ``schema_list_params``, a
    dict or a ``QueryParams`` instead. To skip the methods on purpose, turn
    the warning off with ``warnings.filterwarnings``.
    """


class RestlyDuplicateSchemaNameWarning(UserWarning):
    """Emitted when more than one class in the OpenAPI spec has the same name.

    OpenAPI keeps all classes in one list, by name. So pydantic shows the
    classes under long names that can change, such as
    ``app__tasks__views__TaskCreate``, and generated clients use these names
    as type names. The warning names the classes and the views that use them.

    Restly checks the spec each time the app builds it. It knows the app when
    you pass it to :func:`~fastapi_restly.db.configure` or
    :func:`~fastapi_restly.testing.configure_tests`, or when you include a
    view on the app itself. Then the check also covers the views that you
    include on an ``APIRouter``. There is no setting that turns the check
    off. A project test can turn the warning into an error with
    ``warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)``.
    """


__all__ = [
    "BadQueryParam",
    "Conflict",
    "Forbidden",
    "NotFound",
    "RestlyConfigurationError",
    "RestlyDuplicateSchemaNameWarning",
    "RestlyError",
    "RestlyExpireOnCommitWarning",
    "RestlyHTTPError",
    "RestlyMisuseWarning",
    "RestlySkippedFilterMethodsWarning",
    "RestlyUncommittedChangesWarning",
]
