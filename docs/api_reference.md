# API Reference

The public API documented below is accessible through `fr`, including
submodules such as {mod}`fr.views <fastapi_restly.views>` and
{mod}`fr.objects <fastapi_restly.objects>`. Using `fr.` is the recommended
convention:

```python
import fastapi_restly as fr
```

(full-python-api-autodoc)=
## Module reference

| Module | Contents |
|---|---|
| {mod}`fr <fastapi_restly>` | Names exported directly by the package, linked to their definitions in the submodules. |
| {mod}`fr.views <fastapi_restly.views>` | View classes, route decorators, registration, CRUD methods, and action hooks. |
| {mod}`fr.schemas <fastapi_restly.schemas>` | Pydantic bases, field markers, a field type and a function for times in UTC, relationship references, and schema generation. |
| {mod}`fr.models <fastapi_restly.models>` | Defaults for your declarative base, and mixins for IDs and timestamps. |
| {mod}`fr.db <fastapi_restly.db>` | Configuration, session dependencies, session context managers, and engine access. |
| {mod}`fr.clauses <fastapi_restly.clauses>` | Reusable query predicates, boolean composition, and request context values. |
| {mod}`fr.query <fastapi_restly.query>` | The list params (filter, sort, and pagination), how to apply them to SQLAlchemy queries, and the tools for list params of your own: their keys, their page fields and the primary key as the last sort term. |
| {mod}`fr.objects <fastapi_restly.objects>` | Functions for building, updating, saving, and deleting ORM objects through a session. |
| {mod}`fr.exc <fastapi_restly.exc>` | Configuration errors, HTTP errors, and warnings. |
| {mod}`fr.testing <fastapi_restly.testing>` | Database test setup and synchronous and asynchronous test clients. |

## Reference pages

- [Modules](api/index.rst) documents every module, with complete
  signatures.
- [View Reference](view_reference.md) lists everything you can set, override,
  or call on a view, on one page.
- [Changelog](changelog.md) lists the changes in each release.

```{toctree}
:maxdepth: 2
:hidden:

api/index
view_reference
changelog
```
