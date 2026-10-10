from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

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

# What a request method checks: "2xx" for any success status, an int for that
# exact status, or None for no check.
ExpectedStatus = int | Literal["2xx"] | None


class _StatusAssertions:
    def assert_status(
        self, response: httpx.Response, expected_code: ExpectedStatus = "2xx"
    ) -> None:
        """Check the status code of a response.

        ``"2xx"``, the default, accepts any success status. An int accepts only
        that status. ``None`` accepts every status.
        """
        __tracebackhide__ = True

        status_code = response.status_code

        if expected_code is None or status_code == expected_code:
            return
        if expected_code == "2xx" and 200 <= status_code < 300:
            return

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
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a GET request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = super().get(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def post(
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a POST request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = super().post(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def put(
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a PUT request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = super().put(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def patch(
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a PATCH request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = super().patch(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def delete(
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a DELETE request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
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
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a GET request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = await super().get(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    async def post(
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a POST request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = await super().post(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    async def put(
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a PUT request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = await super().put(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    async def patch(
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a PATCH request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = await super().patch(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    async def delete(
        self,
        url: URLTypes,
        *,
        assert_status_code: ExpectedStatus = "2xx",
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a DELETE request and check its status.

        By default the status must be a success (2xx). Pass an int to require
        that exact status, or ``None`` to skip the check.
        """
        __tracebackhide__ = True
        response = await super().delete(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response
