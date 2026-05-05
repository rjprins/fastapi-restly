Typing compatibility fixtures for Restly consumer code.

These files are not pytest tests. They are small example applications that are
checked with Pyright to verify that normal Restly usage stays quiet in editors
like VS Code with Pylance.

Run them with:

```bash
make test-typing
```

Package inspection baseline:

```bash
uv run pyright fastapi_restly
```

As of 2026-05-05 this reports 26 package-internal errors. The previously
user-facing `ClassVar[type[SchemaT]]` cascade in `views/_base.py` and bare
`_ReactAdminMixin` attribute cascade in `views/_react_admin.py` are fixed. The
remaining package errors are internal dynamic-framework edges: SQLAlchemy model
`id` access through `DeclarativeBase`, dynamic endpoint attributes installed
before route registration, `schema_obj` endpoint annotations tracked by
`fastapi-restly-gfu.29`, one pytest fixture redeclaration, and FastAPI/Starlette
stub mismatches.

Design rules:

- Keep examples focused on public Restly usage, not framework internals.
- Prefer one file per usage pattern.
- Avoid runtime assertions unless a fixture doubles as documentation.
- Name files without the `test_` prefix so pytest does not collect them.

Coverage checklist:

- [x] Async CRUD view with auto schema
- [x] Async CRUD view with explicit schema, including bare `IDSchema` subclassing
- [x] Relationship IDs with `IDSchema[...]`
- [x] Sync `RestView` with explicit create/update schemas
- [x] Custom extra routes via `@fr.get` and `@fr.route`
- [x] `handle_*` handler overrides
- [x] View options: `include_pagination_metadata`
- [x] View options: `exclude_routes`
- [x] `AsyncReactAdminView`
- [ ] `ReactAdminView` sync variant
- [ ] Inherited view configuration from base classes
- [ ] Prefix concatenation across inherited views
- [ ] Class-level `dependencies`
- [ ] Instance dependencies via annotated `Depends`
- [x] Schema aliases with list-params filtering
- [x] `IDRef[...]`
- [x] UUID / non-int primary key flows
- [ ] Write-only and read-only field markers in consumer schemas
- [ ] Direct override of built-in `get/post/patch/delete` route methods as a documented pattern
