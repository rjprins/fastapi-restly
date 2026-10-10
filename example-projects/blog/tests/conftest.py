import os
from pathlib import Path

# blog.main configures Restly when it is imported, so choose the test
# database first, as the tutorial's Testing section does.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"

from blog.main import app  # noqa: E402

import fastapi_restly as fr  # noqa: E402

# The framework under test must live in this checkout, else a leaked VIRTUAL_ENV
# (e.g. the main framework .venv) silently validates the wrong source in a worktree.
_checkout = Path(__file__).resolve().parents[3]
_frl = Path(fr.__file__).resolve()
if _checkout not in _frl.parents:
    raise RuntimeError(
        f"fastapi_restly under test is {_frl}, outside this checkout ({_checkout}). "
        f"This example's venv isn't synced to this tree — run `uv sync` here."
    )

fr.testing.configure_tests(app=app, base=fr.IDBase, create_all=True)
