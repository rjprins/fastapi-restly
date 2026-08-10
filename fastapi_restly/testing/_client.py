from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

try:
    from fastapi.testclient import TestClient
except RuntimeError as exc:  # pragma: no cover -- exercised via subprocess
    # Starlette >=1.x raises RuntimeError (not ModuleNotFoundError) at import
    # when neither httpx2 nor httpx is installed. Normalize it to a missing
    # module so the importing namespaces surface the [testing] extras hint
    # instead of Starlette's bare "pip install httpx2" message.
    if "httpx" not in str(exc):
        raise
    raise ModuleNotFoundError(name="httpx2") from exc

if TYPE_CHECKING:
    # httpx2 is a drop-in rename of httpx 0.28.1 with identical stubs, so we
    # type-check against httpx and let runtime pick whichever is installed.
    import httpx
else:
    # Match Starlette's TestClient, which prefers httpx2 and only falls back to
    # httpx (with a deprecation warning) when httpx2 is absent. Aligning our
    # reference keeps httpx.Response/httpx.URL the same classes the parent
    # TestClient yields.
    try:
        import httpx2 as httpx
    except ModuleNotFoundError:
        import httpx

# httpx accepts either a string or a `httpx.URL` for request URLs. The base
# class' `URLTypes` alias is private, so we replicate the public union here.
URLTypes = httpx.URL | str


class _StatusAssertions:
    def assert_status(
        self, response: httpx.Response, expected_code: int | None = None
    ) -> None:
        """Check if the response status code matches the expected code."""
        __tracebackhide__ = True

        status_code = response.status_code

        if expected_code is not None and status_code == expected_code:
            return  # All good

        if expected_code is None and status_code < 400:
            return  # Also fine

        # Raise AssertionError with detailed error message
        try:
            response_content = response.json()
        except (ValueError, TypeError, json.JSONDecodeError):
            response_content = response.content.decode(errors="ignore")

        content_str_raw = str(response_content)
        if len(content_str_raw) > 1000:
            content_str_raw = content_str_raw[:1000] + "...(truncated)"
        content_str = f"Response JSON: {content_str_raw}"

        # Safe method/URL extraction
        try:
            method = response.request.method.upper()
            url = str(response.request.url)
            request_info = f"{method} {url}"
        except Exception:
            request_info = "(request info unavailable)"

        raise AssertionError(
            f"Expected {request_info} to return {expected_code}, got {status_code}\n"
            f"{content_str}"
        )


class RestlyTestClient(_StatusAssertions, TestClient):
    """Synchronous test client with Restly's response status assertions."""

    def __init__(
        self, app: Any, *args: Any, _transport_app: Any | None = None, **kwargs: Any
    ) -> None:
        """Build a client while optionally exposing a different public app.

        Managed database isolation wraps the ASGI lifespan internally, but
        callers have always used ``client.app`` as the original ``FastAPI``
        instance (including to register a view after constructing the client).
        ``_transport_app`` lets the fixture keep that contract while Starlette's
        transport and lifespan run the internal wrapper.
        """
        self._restly_public_app = app
        self._restly_transport_app = app if _transport_app is None else _transport_app
        super().__init__(self._restly_transport_app, *args, **kwargs)
        self.app = self._restly_public_app

    def __enter__(self) -> RestlyTestClient:
        # Starlette's lifespan coroutine reads ``self.app`` when __enter__ starts
        # it. Temporarily expose the transport wrapper, then restore the public
        # FastAPI app after startup has completed. The request transport already
        # captured the wrapper in TestClient.__init__.
        self.app = self._restly_transport_app
        try:
            super().__enter__()
        finally:
            self.app = self._restly_public_app
        return self

    def get(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs: Any
    ) -> httpx.Response:
        """Make a GET request. Asserts the response status code matches `assert_status_code` (default: 200).
        Pass `assert_status_code=None` to accept any status below 400 instead;
        it does not skip the check."""
        __tracebackhide__ = True
        response = super().get(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def post(
        self, url: URLTypes, *, assert_status_code: int | None = 201, **kwargs: Any
    ) -> httpx.Response:
        """Make a POST request. Asserts the response status code matches `assert_status_code` (default: 201).
        Pass `assert_status_code=None` to accept any status below 400 instead;
        it does not skip the check."""
        __tracebackhide__ = True
        response = super().post(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def put(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs: Any
    ) -> httpx.Response:
        """Make a PUT request. Asserts the response status code matches `assert_status_code` (default: 200).
        Pass `assert_status_code=None` to accept any status below 400 instead;
        it does not skip the check."""
        __tracebackhide__ = True
        response = super().put(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def patch(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs: Any
    ) -> httpx.Response:
        """Make a PATCH request. Asserts the response status code matches `assert_status_code` (default: 200).
        Pass `assert_status_code=None` to accept any status below 400 instead;
        it does not skip the check."""
        __tracebackhide__ = True
        response = super().patch(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def delete(
        self, url: URLTypes, *, assert_status_code: int | None = 204, **kwargs: Any
    ) -> httpx.Response:
        """Make a DELETE request. Asserts the response status code matches `assert_status_code` (default: 204).
        Pass `assert_status_code=None` to accept any status below 400 instead;
        it does not skip the check."""
        __tracebackhide__ = True
        response = super().delete(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response


class AsyncRestlyTestClient(_StatusAssertions, httpx.AsyncClient):
    """Async test client with the same response assertions as the sync client."""

    def __init__(
        self, app: Any, *args: Any, _transport_app: Any | None = None, **kwargs: Any
    ) -> None:
        self.app = app
        transport_app = app if _transport_app is None else _transport_app
        kwargs.setdefault("transport", httpx.ASGITransport(app=transport_app))
        kwargs.setdefault("base_url", "http://testserver")
        kwargs.setdefault("follow_redirects", True)
        super().__init__(*args, **kwargs)

    async def get(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs: Any
    ) -> httpx.Response:
        """Make a GET request and assert status 200 by default.

        Pass ``assert_status_code=None`` to accept any status below 400; it does
        not skip the check.
        """
        __tracebackhide__ = True
        response = await super().get(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    async def post(
        self, url: URLTypes, *, assert_status_code: int | None = 201, **kwargs: Any
    ) -> httpx.Response:
        """Make a POST request and assert status 201 by default.

        Pass ``assert_status_code=None`` to accept any status below 400; it does
        not skip the check.
        """
        __tracebackhide__ = True
        response = await super().post(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    async def put(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs: Any
    ) -> httpx.Response:
        """Make a PUT request and assert status 200 by default.

        Pass ``assert_status_code=None`` to accept any status below 400; it does
        not skip the check.
        """
        __tracebackhide__ = True
        response = await super().put(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    async def patch(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs: Any
    ) -> httpx.Response:
        """Make a PATCH request and assert status 200 by default.

        Pass ``assert_status_code=None`` to accept any status below 400; it does
        not skip the check.
        """
        __tracebackhide__ = True
        response = await super().patch(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    async def delete(
        self, url: URLTypes, *, assert_status_code: int | None = 204, **kwargs: Any
    ) -> httpx.Response:
        """Make a DELETE request and assert status 204 by default.

        Pass ``assert_status_code=None`` to accept any status below 400; it does
        not skip the check.
        """
        __tracebackhide__ = True
        response = await super().delete(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response
