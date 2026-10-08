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

- Write for readers whose first language is not English. Use common words,
  not rare or formal ones such as "canonical" or "superfluous", and no
  idioms. Keep sentences short, with one idea each. Then read the draft again
  word by word, and replace anything a tired reader would stumble on. This
  applies to the docs, docstrings, error messages and the changelog.
- Update relevant docs when user-facing behaviour changes; the docs build is
  warning-clean and CI enforces it (`make docs` runs Sphinx with `-W`).
- One page owns each topic; other pages link to it instead of restating.
  The three-tier override model is owned by `customize.md`; the query
  grammar by `howto_query_modifiers.md`; schema bases by
  `howto_custom_schema.md`. `howto_current.md` owns request context and
  dependency binding. `clauses.md` owns query clauses and their integration
  with `Current`.
- Link API symbols to their autodoc entry with MyST roles:
  ``{class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` `` —
  prose mentions of Restly objects should be clickable, SQLAlchemy-style.
  External objects resolve via intersphinx (python / sqlalchemy / pydantic /
  fastapi).
- Link to a section on another page through an explicit MyST target: put
  `(name)=` above the heading and link with `[text](#name)`, with no
  filename. The two anchor forms are not interchangeable: `file.md#x`
  resolves auto-generated heading slugs only (`myst_heading_anchors = 3`),
  so it cannot reach a `(name)=` target, and it breaks whenever the
  heading is reworded. Both mistakes fail the build: `nitpicky = True`
  plus `-W` turns an unresolved link or role into an error.
- Code examples in docs should be runnable as shown (or clearly marked
  illustrative); the landing-page teaser, the tutorial code, and Patterns
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

## Naming Conventions

Public names follow these rules. Check a new name against them before you
add it.

1. Actions are verbs: `get_many`, `get_one`, `create`, `update`, `delete`.
   These names stay.
2. "List" is what `get_many` returns: the result (`ListResult`), the response
   shape (`ResponseShape.LIST`), the list response (`to_list_response`) and
   the list params (`schema_list_params`).
3. An attribute puts the noun first and the role after, so it does not look
   like a method: `schema_create`, `schema_list_params`.
4. A function starts with a verb. It starts with an action name only if it is
   part of that action: `update_object` is fine, `create_schema_from_model` is
   not. Getters such as `get_relationship_loader_options` are fine.
5. A function that builds a schema attribute is named `derive_` plus the
   attribute name: `derive_schema_list_params` builds `schema_list_params`.
6. A class that Restly generates is named `<Resource><Role>`. Resource is the
   class name of the view's schema without a final `Schema` or `Response`.
   The client sees these names: `UserResponse`, `UserCreate`, `UserUpdate`
   and `UserListResponse`. It sees the view's schema only when another schema
   nests it or a custom route names it. A view without a schema gets a
   generated `UserSchema`.
7. One word per thing. "Response" is what goes out. "List params" is the list
   grammar and its value. "Query params" only means raw keys from the URL, as
   in `extra_query_params`.

A public function takes the same argument names as the view: `schema`, not
`schema_cls`; `query`, not `select_query`; `list_params`, not `params`. When
a view method and a public function do the same thing, they share the name,
and the arguments they share come in the same order: the view method
`apply_list_params(query, list_params)` and the function
`fr.query.apply_list_params(query, list_params, model, schema)`.

### The view's schema

`schema` is the view's schema, next to `model`. It is not the response
schema: it can hold `WriteOnly` fields, and those never go out. Restly
derives the response, create, update and list params schemas from it.

- In docs, call it "the view's schema". Do not call it "resource schema":
  "resource" and "response" look too much alike. Use "response schema" only
  for what really goes out.
- Schemas written by hand use the same names as generated ones: `UserSchema`
  for the view's schema, and `UserCreate` and `UserUpdate` for the roles. A
  hand-written `UserResponse` is the view's `schema`: Restly drops the final
  `Response` from the resource name, so the response class keeps that name.
  The docs and examples teach these names.
- A class with a role name is set on the view, as in `schema_create`, so
  there is only one. A class for another purpose gets a name that says what
  it is, such as `UserSummary`. OpenAPI has no modules, so each class name
  must be unique in the app.

### Words we do not use

- "listing": in everyday English, a listing is one item on offer. Say "list":
  the list endpoint, the list response.
- "Read" as a role: say "Response". It matches FastAPI's `response_model`.
- "singular" or "item" for one object: say "single". The docs use `Item` as
  an example model.
- `list` as a method name: it hides the builtin `list` in the class body.

## Release Process

Releases are cut by maintainers. The flow is:

1. Move entries from `## [Unreleased]` into a new dated section in
   `CHANGELOG.md` (e.g. `## [0.2.0] - YYYY-MM-DD`) and update the comparison
   links at the bottom of the file.
2. Bump `version` in `pyproject.toml`.
3. Regenerate the scaffold snapshot, which floors its `fastapi-restly`
   requirement at the generating version:

   ```bash
   rm -r example-projects/starter
   uv run python -c "from pathlib import Path; from fastapi_restly._scaffold._generate import Options, generate; generate(Options(name='myapp'), Path('example-projects/starter'))"
   make scaffold-check
   ```

4. Commit, tag (`git tag vX.Y.Z`), and push the tag.
5. CI publishes the release artifacts.

## Getting Help

- Open a GitHub issue for bugs or feature ideas.
- For security issues, see [`SECURITY.md`](SECURITY.md) - please do not open
  a public issue.
