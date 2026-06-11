# Contributing to FastAPI-Restly

Thanks for your interest in contributing! FastAPI-Restly is a small,
opinionated framework and we welcome bug reports, fixes, docs improvements,
and well-scoped feature proposals.

## Development Setup

The project uses [uv](https://github.com/astral-sh/uv) for dependency
management. Once you have `uv` installed:

```bash
git clone https://github.com/rjprins/fastapi-restly.git
cd fastapi-restly
make install-dev
```

This installs the framework in editable mode along with development
dependencies.

## Running Tests

```bash
# Framework tests only (fast)
make test-framework

# Framework tests + example projects
make test-all

# Type-checking tests
make test-typing

# Run a single test file
uv run pytest tests/test_schemas.py -v

# Run a single test
uv run pytest tests/test_schemas.py::test_function_name -v
```

Tests use savepoint-based isolation, so test data does not persist between
tests.

## Linting and Formatting

The project uses [ruff](https://docs.astral.sh/ruff/) for both linting and
formatting:

```bash
uv run ruff check .
uv run ruff format .
```

A `pre-commit` config is included; install hooks with `uv run pre-commit
install` to run checks automatically on each commit.

## Building the Docs

```bash
# One-shot build
make docs

# Live-reload server
make docs-serve
```

## Documentation Conventions

- Update relevant docs when user-facing behaviour changes; the docs build is
  warning-clean and CI enforces it (`make docs` runs Sphinx with `-W`).
- One page owns each topic; other pages link to it instead of restating.
  The three-tier override model is owned by `the_handle_design.md`; the
  query grammar by `howto_query_modifiers.md`; schema bases by
  `howto_custom_schema.md`.
- Link API symbols to their autodoc entry with MyST roles:
  ``{class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` `` —
  prose mentions of Restly objects should be clickable, SQLAlchemy-style.
  External objects resolve via intersphinx (python / sqlalchemy / pydantic /
  fastapi).
- Link to a section on another page through an explicit MyST target
  (`(name)=` above the heading, `[text](#name)` to link) — heading-slug
  fragments (`file.md#some-heading`) break silently when headings change.
- Code examples in docs should be runnable as shown (or clearly marked
  illustrative); the landing-page teaser, tutorial listing, and Patterns
  entries are executed during review — keep them that way.

## Pull Request Conventions

- Link the PR to a related issue when one exists. If no issue exists for a
  non-trivial change, open one first to discuss.
- Keep PRs small and focused. One logical change per PR makes review and
  bisecting easier.
- Add tests for new behaviour or bug fixes. Tests should fail before your
  change and pass after.
- Update [`CHANGELOG.md`](CHANGELOG.md) under `## [Unreleased]` with a short
  entry under the appropriate heading (Added / Changed / Fixed / Removed).
- Update relevant docs (`docs/`) when user-facing behaviour changes.
- Make sure `uv run ruff check .` and `make test-framework` pass locally.
- Use clear commit messages. The existing log uses lightweight prefixes such
  as `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:` -
  please follow that style.

## Code Style

- Code is auto-formatted by `ruff format`; do not hand-format around it.
- Public functions, methods, and class attributes should be type-hinted.
  The project supports Python 3.10+ and uses modern typing features.
- Prefer composition and small modules over deep inheritance.
- Submodules under `fastapi_restly/` are organized by layer (`views/`,
  `schemas/`, `db/`, `models/`, `query/`, `testing/`). Keep new code in the
  layer it belongs to.
- Internal modules are prefixed with `_` (e.g. `_base.py`, `_async.py`) and
  re-exported from package `__init__.py`.

## Release Process

Releases are cut by maintainers. The flow is:

1. Move entries from `## [Unreleased]` into a new dated section in
   `CHANGELOG.md` (e.g. `## [0.2.0] - YYYY-MM-DD`) and update the comparison
   links at the bottom of the file.
2. Bump `version` in `pyproject.toml`.
3. Commit, tag (`git tag vX.Y.Z`), and push the tag.
4. CI publishes the release artifacts.

## Getting Help

- Open a GitHub issue for bugs or feature ideas.
- For security issues, see [`SECURITY.md`](SECURITY.md) - please do not open
  a public issue.
