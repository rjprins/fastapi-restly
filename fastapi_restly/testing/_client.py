import json
from typing import TYPE_CHECKING

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


class RestlyTestClient(TestClient):
    """Custom TestClient that automatically checks response codes and provides clear error messages."""

    def assert_status(self, response: httpx.Response, expected_code: int | None = None):
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

    def get(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs
    ) -> httpx.Response:
        """Make a GET request. Asserts the response status code matches `assert_status_code` (default: 200).
        Pass `assert_status_code=None` to skip the assertion."""
        __tracebackhide__ = True
        response = super().get(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def post(
        self, url: URLTypes, *, assert_status_code: int | None = 201, **kwargs
    ) -> httpx.Response:
        """Make a POST request. Asserts the response status code matches `assert_status_code` (default: 201).
        Pass `assert_status_code=None` to skip the assertion."""
        __tracebackhide__ = True
        response = super().post(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def put(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs
    ) -> httpx.Response:
        """Make a PUT request. Asserts the response status code matches `assert_status_code` (default: 200).
        Pass `assert_status_code=None` to skip the assertion."""
        __tracebackhide__ = True
        response = super().put(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def patch(
        self, url: URLTypes, *, assert_status_code: int | None = 200, **kwargs
    ) -> httpx.Response:
        """Make a PATCH request. Asserts the response status code matches `assert_status_code` (default: 200).
        Pass `assert_status_code=None` to skip the assertion."""
        __tracebackhide__ = True
        response = super().patch(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def delete(
        self, url: URLTypes, *, assert_status_code: int | None = 204, **kwargs
    ) -> httpx.Response:
        """Make a DELETE request. Asserts the response status code matches `assert_status_code` (default: 204).
        Pass `assert_status_code=None` to skip the assertion."""
        __tracebackhide__ = True
        response = super().delete(url, **kwargs)
        self.assert_status(response, assert_status_code)
        return response
