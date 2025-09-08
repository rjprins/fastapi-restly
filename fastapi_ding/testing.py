import httpx
from fastapi.testclient import TestClient


class DingTestClient(TestClient):
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
            content_str = f"Response JSON: {response_content}"
        except:
            content_str = (
                f"Response content: {response.content.decode(errors='ignore')}"
            )
        # TODO: Make this more robust: response.request can fail
        raise AssertionError(
            f"Expected {response.request.method.upper()} to return {expected_code}, got {status_code}\n"
            f"{content_str}"
        )

    def get(self, *args, assert_status_code: int = 200, **kwargs):
        __tracebackhide__ = True
        response = super().get(*args, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def post(self, *args, assert_status_code: int = 201, **kwargs):
        __tracebackhide__ = True
        response = super().post(*args, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def put(self, *args, assert_status_code: int = 200, **kwargs):
        __tracebackhide__ = True
        response = super().put(*args, **kwargs)
        self.assert_status(response, assert_status_code)
        return response

    def delete(self, *args, assert_status_code: int = 204, **kwargs):
        __tracebackhide__ = True
        response = super().delete(*args, **kwargs)
        self.assert_status(response, assert_status_code)
        return response
