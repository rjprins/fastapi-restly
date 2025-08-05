from fastapi.testclient import TestClient


class DingTestClient(TestClient):
    """Custom TestClient that automatically checks response codes and provides clear error messages."""

    def _check_status_code(
        self, method: str, status_code: int, response, expected_code: int = None
    ):
        """Check if the response status code matches the expected code for the HTTP method."""
        if expected_code is None:
            return

        if status_code != expected_code:
            # Try to get JSON response, fallback to content
            try:
                response_content = response.json()
                content_str = f"Response JSON: {response_content}"
            except:
                content_str = f"Response content: {response.content.decode('utf-8', errors='ignore')}"

            error_msg = (
                f"Expected {method.upper()} to return {expected_code}, got {status_code}\n"
                f"{content_str}"
            )
            raise AssertionError(error_msg)

    def get(self, *args, assert_status_code: int = 200, **kwargs):
        response = super().get(*args, **kwargs)
        self._check_status_code(
            "GET", response.status_code, response, assert_status_code
        )
        return response

    def post(self, *args, assert_status_code: int = 201, **kwargs):
        response = super().post(*args, **kwargs)
        self._check_status_code(
            "POST", response.status_code, response, assert_status_code
        )
        return response

    def put(self, *args, assert_status_code: int = 200, **kwargs):
        response = super().put(*args, **kwargs)
        self._check_status_code(
            "PUT", response.status_code, response, assert_status_code
        )
        return response

    def delete(self, *args, assert_status_code: int = 204, **kwargs):
        response = super().delete(*args, **kwargs)
        self._check_status_code(
            "DELETE", response.status_code, response, assert_status_code
        )
        return response
