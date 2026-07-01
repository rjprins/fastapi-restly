"""Public exception hierarchy for FastAPI-Restly.

Two families:

* Configuration-time errors (:class:`RestlyError` / :class:`RestlyConfigurationError`)
  raised when the framework is misused before/at setup.
* Request-time HTTP errors (:class:`NotFound` / :class:`Forbidden` /
  :class:`Conflict` / :class:`BadQueryParam`) raised while handling a request.
  These subclass :class:`fastapi.HTTPException`, so the default responses are
  identical to raising ``HTTPException`` directly -- but a user can
  ``app.add_exception_handler(fr.exc.NotFound, ...)`` to reshape Restly's errors
  distinctly (e.g. into RFC 7807 problem+json).
"""

import fastapi


class RestlyError(Exception):
    """Base class for FastAPI-Restly framework errors."""


class RestlyConfigurationError(RestlyError):
    """Raised when Restly is used before required configuration is available."""


class RestlyHTTPError(fastapi.HTTPException):
    """Base for Restly's request-time HTTP errors. Subclasses set a status."""

    status_code: int = 500
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
    """HTTP 409 -- the request conflicts with the current resource state."""

    status_code = 409
    default_detail = "Conflict"


class BadQueryParam(RestlyHTTPError):
    """HTTP 400 -- an invalid filter/sort/pagination query parameter."""

    status_code = 400
    default_detail = "Invalid query parameter"


class RestlyUncommittedChangesWarning(UserWarning):
    """Emitted when a request finishes with uncommitted changes in the session.

    The write handlers own the commit, so a custom write route that flushes
    (e.g. via ``save_object``) but never commits has its changes silently
    rolled back when the session closes. The fix is almost always to commit:
    bracket the mutation with ``write_action(...)`` (the framework then runs
    the commit), or reuse a ``handle_<verb>`` handler. A route that
    *intentionally* rolls back (a validate-then-rollback dry run) can suppress
    the warning for just that request with
    ``session.info["_fr_suppress_uncommitted"] = True``.
    """


class RestlyMisuseWarning(UserWarning):
    """Emitted at view registration for common framework-misuse patterns.

    Opt-in: enable with ``fr.configure(warn_on_misuse=True)``. When a view
    class is registered via ``include_view``, the framework then flags the
    dominant misuses -- overriding a route shell (``<verb>_endpoint``) where a
    business-verb override was meant, calling ``session.commit()`` directly in a
    view method, hand-rolling a CRUD route set on a bare ``View`` instead of
    subclassing ``RestView`` / ``AsyncRestView``, and typing a scalar
    foreign-key column (``post_id``) as an ``IDRef`` / ``IDSchema`` reference
    instead of ``fr.MustExist[int, Model]``.
    """


__all__ = [
    "BadQueryParam",
    "Conflict",
    "Forbidden",
    "NotFound",
    "RestlyConfigurationError",
    "RestlyError",
    "RestlyHTTPError",
    "RestlyMisuseWarning",
    "RestlyUncommittedChangesWarning",
]
