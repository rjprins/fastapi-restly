# Contributing to FastAPI-Restly

Thanks for your interest in contributing! FastAPI-Restly is a small framework
with strong opinions. We welcome bug reports, fixes, improvements to the docs,
and feature proposals with a clear scope.

## Development Setup

The project uses [uv](https://github.com/astral-sh/uv) to manage
dependencies. When you have `uv`, run:

```bash
git clone https://github.com/rjprins/fastapi-restly.git
cd fastapi-restly
make install-dev
```

This installs the framework in editable mode, together with the development
dependencies.

## Running Tests

```bash
# Framework tests only (fast)
make test-framework

# Framework tests + example projects
make test-all

# Type-checking tests
make test-typing

# The built package, installed outside the repo: README quickstart, tutorials
make fresh-install-check

# Run a single test file
uv run pytest tests/test_schemas.py -v

# Run a single test
uv run pytest tests/test_schemas.py::test_function_name -v
```

Each test runs inside a savepoint, so the data of one test never reaches
another test.

## Linting and Formatting

The project uses [ruff](https://docs.astral.sh/ruff/) to lint and to format
the code:

```bash
uv run ruff check .
uv run ruff format .
```

The repo has a `pre-commit` config. Run `uv run pre-commit install` once, and
the checks run on each commit.

## Building the Docs

```bash
# Build once
make docs

# Serve the docs, and build again on each change
make docs-serve
```

## Documentation Conventions

### Language

These rules apply to the docs, docstrings, error messages and the changelog.

- Write for readers whose first language is not English. Use common words,
  not rare or formal ones such as "canonical" or "superfluous". Do not use
  idioms.
- Keep sentences short, with one idea in each.
- When the draft is done, read it again word by word. Replace anything that a
  tired reader would stumble on.
- Introduce a thing before you refer to it. Words such as "the", "this",
  "it", "its" and "they" point back to something. The reader must have met
  that thing earlier on the same page. Check each of these words, also each
  "the", and ask: "Which one? Where did the reader see it?" If there is no
  clear answer, use "a", name the thing in full, or introduce it first.
- The first time a page uses a Restly term, such as "view" or "action",
  explain it in a few words, or link to the page that explains it. This
  matters most at the top of a page, where the reader knows the least.
  Explain only what the reader needs at that point. A short, rough idea is
  better than a full explanation too early.
- Describe a thing by what it does or what it is for. A sentence that only
  says what a thing does not have, such as "has no routes of its own", gives
  the reader no reason to use it. If the missing part matters, say it after
  the purpose.

### Pages and sections

- A Guide section starts with its lesson: the common case, done the
  recommended way. This is usually one or two short paragraphs and one code
  block. After the lesson, put everything else in one Details block at the
  end of the section: less common cases, other ways to do the same thing,
  and reasons that the reader does not need in order to act.

  ```text
  :::{admonition} Details
  :class: details

  ...
  :::
  ```

  Never put a warning about a security bug or lost data in Details. A short
  section has no Details block.
- Each topic has one page that owns it. Other pages link to that page; they
  do not explain the topic again.
- The Guide front page (`docs/guide.md`) lists each Guide page in its group.
  Each page gets one line that names its main topics. The docs build writes
  `llms.txt` from this front page, and it fails when a Guide page has no
  line.

### Names and links

- Write a Restly name as the reader types it in code: `fr.View`, not "View"
  or "the View class".
- In prose, link each Restly object to its entry in the API reference, as the
  SQLAlchemy docs do. Use a MyST role:
  ``{class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` ``. Use
  the same roles for objects of Python, SQLAlchemy, Pydantic and FastAPI;
  intersphinx finds their entries.
- To link to a section on another page, put a target `(name)=` above the
  heading. Link to it with `[text](#name)`, without a file name. Do not use
  `file.md#x`: this form only finds the anchors that MyST makes from the
  heading text (`myst_heading_anchors = 3`). It cannot find a `(name)=`
  target, and it breaks when someone changes the heading.
- The docs build must give no warnings, and CI checks this. `make docs` runs
  Sphinx with `-W` and `nitpicky = True`, so a link or a role that points
  nowhere fails the build.

### Code examples

- A code example in the docs runs as shown. If it cannot run, say clearly
  that it only shows the idea.
- The code on the landing page (`docs/index.md`) is run by hand when a PR is
  reviewed. Keep it that way.
- The tutorials include their full files from `example-projects/blog`.
  `tests/test_tutorial.py` sends the requests that the tutorials show to
  these files. When you change one of these files, change the test with it.
- No test covers two things, so check them by hand: a response from an
  earlier state, such as the error before a fix, and a code block that the
  tutorial does not include from a file.

## Pull Request Conventions

- If an issue exists for your change, link it from the PR. For a larger
  change without an issue, open an issue first, so that we can discuss it.
- Make one change per PR. A small PR is easier to review, and easier to find
  with `git bisect` when something breaks.
- Add tests for new behaviour and for bug fixes. A new test fails without
  your change and passes with it.
- Add a short entry to [`CHANGELOG.md`](CHANGELOG.md) under
  `## [Unreleased]`, below the right heading: Added, Changed, Fixed or
  Removed.
- When behaviour that users see changes, update the docs in `docs/`.
- Before you open the PR, run `uv run ruff check .` and
  `make test-framework`. Both must pass.
- Start each commit message with a prefix, as the existing log does: `feat:`,
  `fix:`, `docs:`, `chore:`, `refactor:`, `test:` or `ci:`. Add `!` for a
  change that breaks existing code, as in `feat!:`.

## Code Style

- `ruff format` formats all code. Do not change its output by hand.
- Add type hints to public functions, methods and class attributes. The
  project supports Python 3.11 and later, so use the typing features of
  Python 3.11, such as `X | None`.
- Prefer composition and small modules to deep inheritance.
- The packages under `fastapi_restly/` are layers: `views/`, `schemas/`,
  `db/`, `models/`, `query/` and `testing/`. Put new code in the layer where
  it belongs.
- The name of an internal module starts with `_`, such as `_base.py` or
  `_async.py`. The `__init__.py` of the package exports its public names.

## Naming Conventions

Public names follow these rules. Before you add a new name, check it against
them.

1. An action has a verb as its name: `get_many`, `get_one`, `create`,
   `update` and `delete`. Do not rename these five.
2. The word "list" belongs to `get_many`. Use it for what `get_many` reads and
   returns: the result (`ListResult`), the list response
   (`to_list_response`, `list_response_schema`) and the list params
   (`list_params_schema`).
3. A name ends with what it is: `response_schema` is a schema, `id_type` is a
   type, and `create_endpoint` is an endpoint. A marker gets the name of its
   setting, written as a class name: `ResponseSchema` for `response_schema`,
   and `IDType` for `id_type`.
4. A function name starts with a verb. Use the name of an action as that verb
   only when the function is part of that action: `update_object` is fine,
   but `create_schema_from_model` is not. A getter, such as
   `get_loader_options`, is also fine. Two kinds of functions need not start
   with a verb, as in SQLAlchemy (`and_()`, `func.count()`) and FastAPI
   (`@app.get`):
   - A function that builds or converts a value gets the name of the value
     that it returns: `fr.all_of()` returns a clause, `fr.in_utc()` a time in
     UTC, and `key.term()` the term of a sort.
   - A decorator gets the name of what it declares: `@fr.get` declares a
     `GET` route, and `@fr.list_filter` a filter of the list.
5. A function that builds one of the view's schemas is named `derive_` plus
   the name of the setting: `derive_response_schema` builds
   `response_schema`, and `derive_list_params_schema` builds
   `list_params_schema`. `derive_schema` builds `schema`.
6. A schema that Restly generates is named `<Resource><Role>`, such as
   `UserCreate`.
   - `<Resource>` is the class name of the main schema, without a final
     `Schema` or `Response`: `UserSchema` gives `User`.
   - The list response schema and the list params schema take `<Resource>`
     from the response schema. If the response schema follows these rules,
     the result is the same `<Resource>`.
   - A view without a main schema gets a generated one, named like
     `UserSchema`.

   The client sees these names: `UserResponse`, `UserCreate`, `UserUpdate`
   and `UserListResponse`. The client sees the main schema only when another
   schema nests it, or when a custom route names it.
7. Use one word for each thing, and only for that thing:
   - "Response" is what goes out.
   - "List params" are the keys that a list reads from the URL, such as its
     filters, sort and page, together with their values.
   - "Query params" are only the raw keys from the URL, as in
     `unknown_query_params`.
   - "The view's schemas" are these six: the main schema (`schema`), the
     response schema, the list response schema, the create schema, the
     update schema and the list params schema.
   - "Class" is only the Python object, as in "a class that you rename
     later".
   - "Request body" is only what a client really sends.

A public function uses the same argument names as the view: `schema`, not
`schema_cls`; `stmt`, not `query` or `select_query`; `list_params`, not
`params`. An argument that takes the response schema is `response_schema`.

If a view method and a public function do the same thing, they have the same
name. The arguments that they share come in the same order. For example, the
view method `apply_list_params(stmt, list_params)` and the function
`fr.query.apply_list_params(stmt, list_params, model, response_schema)`.

### The main schema

`schema` is the main schema of the view, next to `model`. It is not the
response schema: it can have `WriteOnly` fields, and these never go out.
Restly derives the response, create and update schemas from it.

A view can also set `response_schema` itself. Everything that goes out
follows `response_schema`: the responses, the list params and the
relationships that the view loads. The request bodies follow `create_schema`
and `update_schema`.

- In the docs, say "the main schema", or "the view's main schema" when the
  reader needs more. Do not say "resource schema": "resource" and "response"
  look too much alike. Do not say "root schema": Pydantic has `RootModel`,
  and JSON Schema uses the same words for something else. Say "response
  schema" only for what really goes out.
- A schema that you write by hand gets the same name as a generated one:
  `UserSchema` for the main schema, and `UserResponse`, `UserCreate` and
  `UserUpdate` for the roles. A view can also use a hand-written
  `UserResponse` as its `schema`. Restly then drops the final `Response`
  from the resource name, so the response schema keeps the name
  `UserResponse`. The docs and examples teach these names.
- Give a role name, such as `UserCreate`, only to a class that you set on
  the view, as in `create_schema = UserCreate`. Then only one class has that
  name. A class for another purpose gets a name that says what it is, such
  as `UserSummary`. OpenAPI has no modules, so each class name must be unique
  in the app.

### Words we do not use

- Say "list", not "listing": the list endpoint, the list response. In
  everyday English, a listing is one item on offer.
- Say "Response", not "Read", as the name of a role. "Response" matches
  FastAPI's `response_model`.
- Say "single", not "singular" or "item", for one object. The docs use
  `Item` as an example model.
- Do not name a method `list`: in the class body, it hides the builtin
  `list`.
- Say "list params" for the keys of a list. Do not say "grammar",
  "dialect", "query contract" or "filter syntax", and in prose do not say
  "list parameters". "Dialect" only means the type of database, as in
  SQLAlchemy.

## Release Process

Maintainers make the releases. The steps are:

1. In `CHANGELOG.md`, move the entries from `## [Unreleased]` into a new
   section with the version and the date, such as
   `## [0.2.0] - YYYY-MM-DD`. Update the comparison links at the bottom of
   the file.
2. Raise `version` in `pyproject.toml`.
3. Generate the scaffold snapshot again, after the change of version. The
   snapshot requires `fastapi-restly` at the version that generated it, or a
   later one:

   ```bash
   rm -r example-projects/starter
   uv run python -c "from pathlib import Path; from fastapi_restly._scaffold._generate import Options, generate; generate(Options(name='myapp'), Path('example-projects/starter'))"
   make scaffold-check
   ```

4. Run `make fresh-install-check`. It installs the built package in a new
   folder outside the repo, as the README says. Then it runs the README
   quickstart, both tutorials and a project from `restly new` against that
   package. CI also runs it, on each push to `main`.
5. Commit and push to `main`. Wait until CI passes. Then tag the commit and
   push the tag:

   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

6. Publish a GitHub Release for the tag. The tag alone publishes nothing. The
   release starts `.github/workflows/publish.yml`, which uploads the package
   to PyPI. Use the new changelog section as the release notes, without its
   heading:

   ```bash
   gh release create vX.Y.Z --verify-tag --title vX.Y.Z --notes-file notes.md
   ```

## Getting Help

- Open a GitHub issue for bugs or feature ideas.
- For security issues, see [`SECURITY.md`](SECURITY.md). Please do not open a
  public issue for them.
