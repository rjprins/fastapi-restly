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
| {mod}`fr.schemas <fastapi_restly.schemas>` | Pydantic bases, field markers, relationship references, and schema generation. |
| {mod}`fr.models <fastapi_restly.models>` | SQLAlchemy declarative bases and mixins for IDs and timestamps. |
| {mod}`fr.db <fastapi_restly.db>` | Configuration, session dependencies, session context managers, and engine access. |
| {mod}`fr.clauses <fastapi_restly.clauses>` | Reusable query predicates, boolean composition, and request context values. |
| {mod}`fr.query <fastapi_restly.query>` | Filter, sort, and pagination parameter schemas and their application to SQLAlchemy queries. |
| {mod}`fr.objects <fastapi_restly.objects>` | Functions for building, updating, saving, and deleting ORM objects through a session. |
| {mod}`fr.exc <fastapi_restly.exc>` | Configuration errors, HTTP errors, and warnings. |
| {mod}`fr.testing <fastapi_restly.testing>` | Database test setup and synchronous and asynchronous test clients. |
| {mod}`fr.utils <fastapi_restly.utils>` | Shared settings instances through {class}`CurrentSettingsMixin <fastapi_restly.utils.CurrentSettingsMixin>`. |

```{toctree}
:maxdepth: 2
:hidden:

api/index
api_details
technical_details
changelog
```
