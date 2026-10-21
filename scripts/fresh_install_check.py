"""Install the built package as a reader of the docs does, then run what they run.

The framework suite runs against the source tree. A reader runs against the
package from PyPI. This check closes that gap before a release: it builds the
wheel, installs it in a new folder outside the repo, and runs the README
quickstart, both tutorials and a project from ``restly new`` against it.

Run it through ``make fresh-install-check``. CI runs it with ``--wheel``, on the
wheel that its build job made. It needs only the standard library and uv.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"

# The install commands as the docs give them. The check installs with these,
# with the package name replaced by the wheel, and fails when the docs no
# longer contain them. So the docs and the check cannot drift apart.
INSTALL_COMMAND = 'pip install "fastapi-restly[standard]" aiosqlite'
TESTING_INSTALL_COMMAND = 'pip install "fastapi-restly[testing]"'
INSTALL_DOCS = (README, REPO / "docs" / "index.md", REPO / "docs" / "tutorial.md")
TESTING_INSTALL_DOCS = (README, REPO / "docs" / "tutorial_customizing.md")

# The tutorial test, and the files of example-projects/blog that it loads.
TUTORIAL_TEST = REPO / "tests" / "test_tutorial.py"
TUTORIAL_FILES = ("part1.py", "main.py")

# This pytest plugin goes next to the copied tutorial test. It fails the run
# when fastapi_restly does not come from the venv, so the source tree can never
# pass the check in place of the wheel.
WHEEL_GUARD = """\
import sys
from pathlib import Path

import pytest


def pytest_report_header():
    import fastapi_restly

    return f"fastapi_restly: {fastapi_restly.__file__}"


def pytest_collection_finish(session):
    module = sys.modules.get("fastapi_restly")
    if module is None:
        raise pytest.UsageError("The tutorial test did not import fastapi_restly.")
    path = Path(module.__file__).resolve()
    if not path.is_relative_to(Path(sys.prefix).resolve()):
        raise pytest.UsageError(
            f"fastapi_restly was imported from {path}, not from the installed wheel."
        )
"""

# Files that `restly new` writes from each template overlay. Between them the
# three projects name every overlay, so a template that is missing from the
# wheel fails here, and not at a reader's first command.
GENERATED_PROJECTS = {
    "demo": (
        ["--sqlite", "--create-all"],
        ["app/main.py", "tests/conftest.py", ".gitignore"],
    ),
    "prod": (
        ["--postgres", "--alembic", "--async"],
        ["compose.yaml", "alembic/env.py", "alembic/versions/.gitkeep"],
    ),
    "legacy": (["--postgres", "--alembic", "--sync"], ["alembic/env.py"]),
}


class CheckFailed(Exception):
    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    """Run a command. On failure, raise with the output that a reader would see."""
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env or reader_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        raise CheckFailed(
            f"`{' '.join(command)}` exited with {result.returncode}.", result.stdout
        )
    return result.stdout


def reader_env() -> dict[str, str]:
    # Under `uv run` or an activated venv, VIRTUAL_ENV names the repo's own
    # environment. A reader's shell does not have it, and uv would warn about it.
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    return env


def require_text(path: Path, text: str) -> None:
    if text not in path.read_text("utf-8"):
        raise CheckFailed(
            f"{path.relative_to(REPO)} no longer contains `{text}`. Update the "
            "docs or the command in scripts/fresh_install_check.py, so that the "
            "check installs the package as the docs tell a reader to."
        )


def as_wheel_install(command: str, wheel: Path) -> list[str]:
    """`pip install "fastapi-restly[x]" y` becomes `uv pip install "<wheel>[x]" y`."""
    words = command.replace('"', "").split()
    assert words[:2] == ["pip", "install"], command
    packages = [
        str(wheel) + word.removeprefix("fastapi-restly")
        if word.startswith("fastapi-restly")
        else word
        for word in words[2:]
    ]
    return ["uv", "pip", "install", *packages]


# --- Steps -------------------------------------------------------------------


def build(work: Path) -> Path:
    dist = work / "dist"
    run(["uv", "build", "--out-dir", str(dist)], cwd=REPO)
    (wheel,) = dist.glob("*.whl")
    return wheel


def install(reader: Path, wheel: Path) -> None:
    for path in INSTALL_DOCS:
        require_text(path, INSTALL_COMMAND)
    run(["uv", "venv", "--quiet"], cwd=reader)
    run(as_wheel_install(INSTALL_COMMAND, wheel), cwd=reader, env=venv_env(reader))


def venv_env(reader: Path) -> dict[str, str]:
    # What `source .venv/bin/activate` gives a reader.
    env = reader_env()
    env["VIRTUAL_ENV"] = str(reader / ".venv")
    env["PATH"] = f"{reader / '.venv' / 'bin'}{os.pathsep}{env['PATH']}"
    return env


def readme_quickstart(reader: Path) -> None:
    (reader / "main.py").write_text(quickstart_code(), "utf-8")
    port = free_port()
    log_path = reader / "server.log"
    # The docs say `fastapi dev main.py`. That starts a reloader, which runs the
    # server in a child process and watches the folder. `fastapi run` serves the
    # same app in one process, without the reloader, so it is simple to stop.
    with log_path.open("w") as log:
        server = subprocess.Popen(
            ["fastapi", "run", "main.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=reader,
            env=venv_env(reader),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        base = f"http://127.0.0.1:{port}"
        wait_until_up(server, base, log_path)
        try:
            send_quickstart_requests(base)
        except CheckFailed as error:
            error.output = error.output or log_path.read_text()
            raise
    finally:
        stop(server)


def quickstart_code() -> str:
    text = README.read_text("utf-8")
    match = re.search(r"^## Quickstart\n(.*?)^## ", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise CheckFailed("README.md has no `## Quickstart` section.")
    block = re.search(r"^```python\n(.*?)^```", match.group(1), re.M | re.S)
    if block is None:
        raise CheckFailed("The README quickstart has no Python code block.")
    return block.group(1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until_up(server: subprocess.Popen[bytes], base: str, log: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise CheckFailed(
                f"`fastapi run main.py` exited with {server.returncode}.",
                log.read_text(),
            )
        try:
            urllib.request.urlopen(f"{base}/openapi.json", timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise CheckFailed("The server did not answer within 30 seconds.", log.read_text())


def send_quickstart_requests(base: str) -> None:
    john = request("POST", f"{base}/users", 201, name="John", email="john@example.com")
    request("POST", f"{base}/users", 201, name="Jane", email="jane@example.org")
    fetched = request("GET", f"{base}/users/{john['id']}", 200)
    expect(fetched == john, f"GET /users/{john['id']} returned {fetched}")
    # Filters from the README's list params section that fit the quickstart model.
    for params in ("name=John", "email__icontains=example.com"):
        page = request("GET", f"{base}/users?{params}", 200)
        expect(
            page.get("total_count") == 1 and page.get("data") == [john],
            f"GET /users?{params} returned {page}",
        )


def request(method: str, url: str, status: int, **body: str) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            code, text = response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        code, text = error.code, error.read().decode()
    expect(code == status, f"{method} {url} returned {code}, not {status}: {text}")
    return json.loads(text)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def stop(server: subprocess.Popen[bytes]) -> None:
    # The server runs in its own process group, so this also stops anything
    # that it started.
    if server.poll() is not None:
        return
    os.killpg(server.pid, signal.SIGTERM)
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(server.pid, signal.SIGKILL)
        server.wait()


def tutorials(reader: Path, wheel: Path) -> None:
    for path in TESTING_INSTALL_DOCS:
        require_text(path, TESTING_INSTALL_COMMAND)
    run(as_wheel_install(TESTING_INSTALL_COMMAND, wheel), reader, venv_env(reader))

    # Copy the test and the tutorial files out of the repo, in the same layout,
    # and run pytest there. Run in the repo, pytest would read pyproject.toml,
    # whose `pythonpath = ["."]` puts the source tree first on sys.path, and
    # load tests/conftest.py, which needs the repo's own test helpers. Out
    # here, there is no repo to import from.
    root = reader / "tutorials"
    (root / "tests").mkdir(parents=True)
    shutil.copy(TUTORIAL_TEST, root / "tests")
    (root / "tests" / "conftest.py").write_text(WHEEL_GUARD, "utf-8")
    blog = root / "example-projects" / "blog" / "blog"
    blog.mkdir(parents=True)
    for name in TUTORIAL_FILES:
        shutil.copy(REPO / "example-projects" / "blog" / "blog" / name, blog)
    run(
        ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        root,
        venv_env(reader),
    )


def generated_project(reader: Path, wheel: Path) -> None:
    for name, (flags, files) in GENERATED_PROJECTS.items():
        run(["restly", "new", name, *flags, "--yes"], reader, venv_env(reader))
        missing = [path for path in files if not (reader / name / path).is_file()]
        expect(not missing, f"`restly new {name}` did not write {missing}")

    # Then the steps that the demo project's README gives. `uv sync` would get
    # fastapi-restly from PyPI, where this version is not published yet, so
    # point it at the wheel.
    demo = reader / "demo"
    pyproject = demo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text("utf-8")
        + f'\n[tool.uv.sources]\nfastapi-restly = {{ path = "{wheel}" }}\n',
        "utf-8",
    )
    run(["uv", "sync", "--quiet"], demo)
    shutil.copy(demo / ".env.example", demo / ".env")
    run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider"], demo)


# --- Main --------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fresh-install check.")
    parser.add_argument("--wheel", type=Path, help="test this wheel, do not build")
    parser.add_argument("--keep", action="store_true", help="keep the temp folder")
    args = parser.parse_args()
    if args.wheel and not (args.wheel.is_file() and args.wheel.suffix == ".whl"):
        parser.error(f"--wheel {args.wheel} is not a wheel file")

    work = Path(tempfile.mkdtemp(prefix="restly-fresh-install-"))
    reader = work / "reader"
    reader.mkdir()
    title = ""

    def start(step: str) -> None:
        nonlocal title
        title = step
        print(f"=== {title}", flush=True)

    try:
        start("1. Build the wheel")
        wheel = args.wheel.resolve() if args.wheel else build(work)
        print(f"    {wheel.name}")
        start("2. Install it as the README says")
        install(reader, wheel)
        start("3. Run the README quickstart")
        readme_quickstart(reader)
        start("4. Run the tutorials")
        tutorials(reader, wheel)
        start("5. Run a project from `restly new`")
        generated_project(reader, wheel)
    except CheckFailed as error:
        print(f"\n!!! Fresh-install check FAILED at step {title}.")
        print(f"!!! {error}")
        if error.output:
            print("\nA reader would see this output:\n")
            print(error.output.rstrip())
        return 1
    finally:
        if args.keep:
            print(f"\nThe temp folder is kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("=== Fresh-install check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
