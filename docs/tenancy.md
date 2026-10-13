(tenant-row-scoping)=

# Multi-Tenancy

In a multi-tenant app, each organization reads and writes only its own rows.
The rule has two halves: a filter on every read, and a column that every write
stamps. Both read the tenant from [`Current`](context.md).

The tenant rule is not a [scope](#default-scope). A view or a route can
replace its scope, but the tenant rule must hold under every scope, also
under `fr.clauses.UNSCOPED`, and for every reference check. So it lives on
the SQLAlchemy session. A `do_orm_execute` listener adds a
[`with_loader_criteria`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.with_loader_criteria)
option to every ORM read of an entity or a relationship. Restly's views,
the {mod}`fr.objects <fastapi_restly.objects>` helpers and your own ORM
queries all read through a session. So the rule reaches each of them, with
the limits in [What the listener does not cover](#tenant-listener-limits).

The [SaaS example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas)
runs this rule on all of its tenant models.

## Set up the tenant rule

The `Current` members, the tenant column and the listener:

```python
import sqlalchemy as sa
from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr


class Current(fr.ContextNamespace):
    org_id: fr.ContextParam[int]
    user_id: fr.ContextParam[int]
    is_admin: fr.ContextParam[bool]


class Base(fr.ModelDefaults, orm.DeclarativeBase):
    pass


class TenantOwned(orm.MappedAsDataclass, kw_only=True, eq=False, repr=False):
    """The tenant column: the organization the request acts in."""

    organization_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey("organization.id"), init=False, insert_default=Current.org_id
    )


# Read identity when the SQL executes, not when the option is attached.
# bindparam tests its callable's truth value, which a Current member rejects,
# so these two wrap the member in a lambda.
tenant_org_id = sa.bindparam(
    "tenant_org_id", callable_=lambda: Current.org_id(), type_=sa.Integer
)
tenant_is_admin = sa.bindparam(
    "tenant_is_admin", callable_=lambda: Current.is_admin(), type_=sa.Boolean
)


class TenantCriteria(orm.UserDefinedOption):
    """Marks a statement that already carries the tenant criteria."""

    propagate_to_loaders = True


@sa.event.listens_for(orm.Session, "do_orm_execute")
def _restrict_tenant_rows(state: orm.ORMExecuteState) -> None:
    if not state.is_select or state.is_column_load:
        return
    if any(isinstance(o, TenantCriteria) for o in state.user_defined_options):
        return
    state.statement = state.statement.options(
        TenantCriteria(),
        orm.with_loader_criteria(
            TenantOwned,
            lambda cls: sa.or_(tenant_is_admin, cls.organization_id == tenant_org_id),
            include_aliases=True,
        ),
    )


class Project(TenantOwned, fr.TimestampsMixin, fr.IDMixin, Base):
    name: orm.Mapped[str]
```

The column is the write half. It is a stamped field, as in
[Stamp the fields the server owns](#server-stamped-fields): an insert default
with `init=False`, and {data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>`
on the schema. It reads the same member as the listener, so a row lands in
the organization that the request acts in, whatever code builds it. An admin
who writes into another tenant acts as that tenant, and the stamp follows.
Code that needs the tenant before the flush, such as a mapper event or a
check for a unique name, reads `Current.org_id()` itself, because the insert
default has not run yet.

Bind the values once per request with a generated dependency,
{meth}`Current.depends(...) <fastapi_restly.clauses.ContextNamespace.depends>`,
as
[Bind from a FastAPI dependency](#current-request-binding) shows. The SaaS
example binds them in
[`app/current.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/current.py).
Its sources answer 401 to a request without an identity, so the values are
never `None`. An admin is a flag, `Current.is_admin`, not a missing value.

## What the listener covers

- Relationship loads get the option too, also when their parent was inserted
  and not loaded by a query. Keep the default `propagate_to_loaders=True`, so
  that joined eager loads get the criteria.

A model that reaches its tenant through a relationship needs its own
criterion. The SaaS example has one for `Task` through its project, for
`TaskLabel` through its task and its label, and for `UploadLine` through its
upload. Write each criterion as a lambda. SQLAlchemy analyses a lambda once
and caches it. It processes a plain expression again for every statement,
also for statements that never read the model.

:::{admonition} Details
:class: details

- The listener adds the option also to a query that starts from a model
  without a tenant, because that query can load tenant rows through a
  relationship. `select(Organization).options(joinedload(Organization.users))`
  must filter the users.
- A relationship load receives the options of the statement that loaded its
  parent. Without the `TenantCriteria` marker, the listener would add a second
  copy of each criterion to that load. A chain of loads, such as the subtasks
  of a task, would add one more copy at each level. The marker goes with the
  criteria, so the listener skips a statement that has it.
- [`bindparam(callable_=...)`](https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.bindparam)
  reads the identity only when the SQL uses that parameter. So a query that
  reads no tenant rows needs no identity. A read of tenant rows without an
  identity fails with a `LookupError`, wrapped in SQLAlchemy's
  `StatementError`. The lambdas are needed because `bindparam` tests whether
  its callable is true, and a `ContextParam` raises on that test.
:::

(tenant-listener-limits)=

## What the listener does not cover

The listener filters ORM reads. It is not access control in the database.
Raw SQL, Core table queries and writes need their own checks. The criteria
reach a statement only through an entity that the statement selects or joins:

- `select(func.count()).where(Task.project_id == id)` names `Task` only in
  its `WHERE` clause, so it counts the tasks of every tenant. Add
  `.select_from(Task)`.
- A relationship condition built with `.any()` or `.has()` does not get the
  criteria inside its `EXISTS` subquery. Add the tenant condition there
  yourself, as the SaaS criteria do.

The criteria also do not remove objects that the session has already loaded,
and `session.get()` returns such an object without a query. So keep
`Current.bind(...)` active as long as the session lives, and use a new session
for a different identity.

(tenant-admin-bypass)=

## Let an admin read every tenant

The listener checks `tenant_is_admin`, so an admin reads across tenants
through the same views. A flag that each request binds lets a rule skip its
filter. If the flag is not bound, the read fails with an error. This keeps one
set of routes, but every rule that an admin may skip must check the flag. For
the rules in a scope, the other choice is a second admin view with
[its own scope](#view-scope). That needs more classes, but each view shows in
its class what it can read. A second view does not change the tenant rule,
because the rule lives on the session.

(composing-on-a-view)=

## Put the parts together

A project in the SaaS example follows three rules: the tenant rule, the audit
stamps and soft delete. `TenantOwned` is on this page. `AuditStamped` is in
[Stamp the fields the server owns](#server-stamped-fields), and
`SoftDeletable` and `SoftDeleteMixin` are in
[Soft-delete rows](#soft-delete-mixin).

The model declares its columns, and the model's clause namespace declares the
read rule. A view adds only the `delete` mixin:

```python
class Project(TenantOwned, AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDMixin, Base):
    name: orm.Mapped[str]


class ProjectClauses(fr.ClauseNamespace):
    model = Project

    is_deleted = fr.where_clause(Project.deleted_at.is_not(None))
    default_scope = fr.none_of(is_deleted)  # the session has the tenant rule


@fr.include_view(app)
class ProjectView(SoftDeleteMixin, fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    # no scope declared: reads apply ProjectClauses.default_scope
```

The [default scope](#default-scope) hides deleted rows from every read, so
the update and delete routes return 404 for a deleted row. The session
listener adds the tenant rule to every read and to every reference check.
The columns stamp themselves on every write. The SaaS example runs this view
in
[`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py).

## See also

- [Request Context](context.md): bind the values that the listener and
  the tenant column read.
- [Scopes](scopes.md): the read rules that a view or a route can replace.
- [Models](#server-stamped-fields): column defaults that stamp a value from
  `Current`, such as the author.
