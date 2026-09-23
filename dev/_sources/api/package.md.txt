# Package API

```{py:module} fastapi_restly
```

`fastapi_restly` exports the following names directly as `fr.<name>`.
Each link opens the symbol's reference entry in its defining module.
The [module reference](../api_reference.md) lists the submodules available
through `fr`.

| Name | Description |
|---|---|
| {class}`fr.Action <fastapi_restly.views.Action>` | CRUD action names used by authorization and commit hooks. |
| {func}`fr.all_of <fastapi_restly.clauses.all_of>` | Combine query clauses with AND. |
| {func}`fr.any_of <fastapi_restly.clauses.any_of>` | Combine query clauses with OR. |
| {func}`fr.apply_clauses <fastapi_restly.clauses.apply_clauses>` | Apply query clauses to a SQLAlchemy statement. |
| {class}`fr.AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` | Async CRUD view for the react-admin simple REST protocol. |
| {class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` | CRUD view using an async SQLAlchemy session. |
| {data}`fr.AsyncSessionDep <fastapi_restly.db.AsyncSessionDep>` | FastAPI dependency annotation for an async session. |
| {class}`fr.BaseSchema <fastapi_restly.schemas.BaseSchema>` | Pydantic base configured to read object attributes. |
| {class}`fr.ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>` | Group a model's query clauses and default scope. |
| {func}`fr.configure <fastapi_restly.db.configure>` | Configure database access and application integration. |
| {class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>` | Declare and bind application context values. |
| {class}`fr.ContextParam <fastapi_restly.clauses.ContextParam>` | Declare one typed context value. |
| {class}`fr.DataclassBase <fastapi_restly.models.DataclassBase>` | SQLAlchemy declarative base with dataclass constructors. |
| {func}`fr.delete <fastapi_restly.views.delete>` | Decorate a DELETE endpoint method. |
| {func}`fr.get <fastapi_restly.views.get>` | Decorate a GET endpoint method. |
| {class}`fr.IDBase <fastapi_restly.models.IDBase>` | Dataclass model base with an integer primary key. |
| {class}`fr.IDRef <fastapi_restly.schemas.IDRef>` | Relationship reference serialized as a scalar ID. |
| {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` | Schema base with a read-only ID, or a nested relationship reference when parameterized. |
| {func}`fr.include_view <fastapi_restly.views.include_view>` | Register a view's endpoint methods on an app or router. |
| {class}`fr.ListingResult <fastapi_restly.views.ListingResult>` | Listing objects, total count, and query parameters before response serialization. |
| {class}`fr.MustExist <fastapi_restly.schemas.MustExist>` | Scalar foreign-key annotation that checks the referenced row exists. |
| {func}`fr.none_of <fastapi_restly.clauses.none_of>` | Negate a group of query clauses. |
| {func}`fr.open_async_session <fastapi_restly.db.open_async_session>` | Open an async session outside request handling. |
| {func}`fr.open_session <fastapi_restly.db.open_session>` | Open a synchronous session outside request handling. |
| {func}`fr.patch <fastapi_restly.views.patch>` | Decorate a PATCH endpoint method. |
| {func}`fr.post <fastapi_restly.views.post>` | Decorate a POST endpoint method. |
| {func}`fr.put <fastapi_restly.views.put>` | Decorate a PUT endpoint method. |
| {class}`fr.ReactAdminView <fastapi_restly.views.ReactAdminView>` | Synchronous CRUD view for the react-admin simple REST protocol. |
| {data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>` | Mark a field as excluded from generated create and update schemas. |
| {class}`fr.RefExists <fastapi_restly.schemas.RefExists>` | Validate a reference against a model with an optional scope override. |
| {func}`fr.resolve_scope <fastapi_restly.views.resolve_scope>` | Resolve the read scope for a view or model. |
| {class}`fr.ResponseShape <fastapi_restly.views.ResponseShape>` | Response forms for one object, a listing, or an empty body. |
| {class}`fr.RestView <fastapi_restly.views.RestView>` | CRUD view using a synchronous SQLAlchemy session. |
| {func}`fr.route <fastapi_restly.views.route>` | Decorate an endpoint method with explicit route options. |
| {data}`fr.SessionDep <fastapi_restly.db.SessionDep>` | FastAPI dependency annotation for a synchronous session. |
| {class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` | Model fields for creation and update timestamps. |
| {class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>` | Read-only creation and update timestamps in a schema. |
| {class}`fr.View <fastapi_restly.views.View>` | Base class for a group of endpoint methods. |
| {class}`fr.ViewRoute <fastapi_restly.views.ViewRoute>` | Default CRUD endpoint names for route exclusion. |
| {func}`fr.where_clause <fastapi_restly.clauses.where_clause>` | Declare a reusable query predicate. |
| {class}`fr.WhereClause <fastapi_restly.clauses.WhereClause>` | A reusable predicate that produces a SQLAlchemy expression. |
| {data}`fr.WriteOnly <fastapi_restly.schemas.WriteOnly>` | Mark a field as accepted on input and excluded from serialization. |
| {data}`fr.__version__ <fastapi_restly.__version__>` | Installed package version. |

```{py:data} fastapi_restly.__version__
:type: str

The installed `fastapi-restly` version, read from package metadata.
```
