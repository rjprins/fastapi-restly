Typing compatibility fixtures for Restly consumer code.

These files are not pytest tests. They are small example applications that are
checked with Pyright to verify that normal Restly usage stays quiet in editors
like VS Code with Pylance.

Run them with:

```bash
make test-typing
```

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
- [x] `on_*` hook overrides
- [x] View options: `include_pagination_metadata`
- [x] View options: `exclude_routes`
- [x] View options: `query_modifier_version = QueryModifierVersion.V2`
- [x] `AsyncReactAdminView`
- [ ] `ReactAdminView` sync variant
- [ ] Inherited view configuration from base classes
- [ ] Prefix concatenation across inherited views
- [ ] Class-level `dependencies`
- [ ] Instance dependencies via annotated `Depends`
- [x] Schema aliases with V2 query modifiers
- [x] `IDRef[...]`
- [x] UUID / non-int primary key flows
- [ ] Write-only and read-only field markers in consumer schemas
- [ ] Direct override of built-in `get/post/patch/delete` route methods as a documented pattern
