# FastAPI-Restly: Project Plan

> A REST framework for building maintainable CRUD APIs with pitch-perfect, symmetric APIs.

## Design Philosophy

### 1. Symmetry Above All

APIs should be **symmetric** and **predictable**. If one pattern exists, the parallel pattern should exist too.

```python
# Symmetric: If async exists, sync exists with identical semantics
fr.AsyncAlchemyView  ↔  fr.AlchemyView
fr.AsyncSessionDep   ↔  fr.SessionDep

# Symmetric: If read exists, write exists
fr.ReadOnly[T]       ↔  fr.WriteOnly[T]

# Symmetric: Method pairs
process_get()        ↔  get()           # Logic vs endpoint
make_new_object()    ↔  update_object() # Create vs update
```

**Current focus for 1.0:**
- Keep async/sync semantics aligned as features evolve
- Document behavior clearly (especially update semantics and query filtering)
- Maintain stable full-suite execution across framework and examples

### 2. Progressive Disclosure

The framework should work with **zero configuration** for simple cases, but allow **progressive customization** for complex needs.

```
Level 0: Just declare model → CRUD works
Level 1: Customize schema → Control serialization
Level 2: Override process_*() → Custom business logic
Level 3: Override endpoint → Full control
Level 4: Custom routes → Extend functionality
```

Each level should feel natural, not like "escaping" the framework.

### 3. One Obvious Way

For any task, there should be **one obvious, recommended way**. Multiple approaches are allowed, but one should be clearly canonical.

```python
# The obvious way to create a view:
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    # That's it. Everything else is optional.
```

### 4. Honest Abstractions

Abstractions should **not hide complexity dangerously**. If something can fail, it should be clear how.

- Database operations can fail → session management is explicit
- Validation can fail → Pydantic errors are propagated cleanly
- Auth can fail → No silent permission bypass

### 5. Composable over Monolithic

Features should be **composable**, not tightly coupled.

```python
# Good: Mix and match
class UserView(fr.AsyncAlchemyView):
    permissions = [IsAuthenticated, IsAdmin]  # Composable
    schema = UserSchema                        # Replaceable
    query_version = QueryModifierVersion.V2    # Configurable

# Bad: All-or-nothing features
```

### 6. REST Purity with Pragmatism

Follow REST principles, but not dogmatically. The framework should make **correct REST easy** while allowing **pragmatic escapes**.

```python
# Correct REST: PATCH updates, DELETE deletes
# Pragmatic: Custom actions when needed
@fr.post("/users/{id}/activate")
async def activate_user(self, id: int): ...
```

---

## Quality Standards

### API Design Standards

1. **Naming Consistency**
   - Classes: `PascalCase` (e.g., `AsyncAlchemyView`)
   - Functions/methods: `snake_case` (e.g., `process_get`)
   - Type annotations: Always present
   - Prefixes: `async_` only when distinguishing from sync variant

2. **Parameter Order Convention**
   ```python
   def method(self, required, *, keyword_only=default)
   ```

3. **Return Type Consistency**
   - Single item: Return model/schema directly
   - List: Return `list[T]`
   - Pagination: Return structured response with metadata
   - Errors: Raise `HTTPException` with appropriate status codes

4. **Error Handling**
   - 400: Bad request (validation, malformed)
   - 401: Unauthorized (not authenticated)
   - 403: Forbidden (authenticated but not permitted)
   - 404: Not found
   - 409: Conflict (duplicate, constraint violation)
   - 422: Unprocessable entity (Pydantic validation)

### Code Quality Standards

1. **Type Safety**: 100% type annotated public API
2. **Documentation**: Docstrings on all public functions/classes
3. **Testing**: Every feature has corresponding tests
4. **Async/Sync Parity**: Every async feature has sync equivalent

### Documentation Standards

1. **README**: Quick start, philosophy, basic examples
2. **API Reference**: Generated from docstrings
3. **Guide**: Progressive tutorials from simple to advanced
4. **Examples**: Real-world example projects

---

## Current State Assessment

### Strengths

1. **Solid Foundation**: Built on FastAPI, SQLAlchemy 2.0, Pydantic v2
2. **Clean View Architecture**: Endpoint/process separation
3. **Schema Generation**: Automatic from SQLAlchemy models
4. **Query Systems**: Two flexible filtering approaches
5. **Testing Utilities**: Savepoint isolation

### Issues to Address

| Priority | Issue | Impact |
|----------|-------|--------|
| P0 | Final API docs for public endpoints | Release blocker for 1.0 |
| P0 | "Getting Started" docs are incomplete | Onboarding friction |
| P0 | Missing feature-specific How-To guides | Adoption risk |
| P1 | CI quality gates must stay green on 3.10-3.13 | Release confidence |
| P1 | Roadmap/PRD drift from implementation | Planning confusion |
| P2 | Authentication/permissions are post-1.0 scope | Future roadmap |

---

## Realistic Example Project: Multi-Tenant SaaS

To validate the framework can handle real-world complexity, we'll build a **multi-tenant project management SaaS** with:

### Domain Model

```
Organization (tenant)
├── Users (with roles: owner, admin, member)
├── Projects
│   ├── Tasks
│   │   ├── Comments
│   │   └── Attachments
│   └── Members (project-level permissions)
└── Invitations
```

### Complexity Checklist

This example validates the framework handles:

- [ ] **Multi-tenancy**: Organization scoping on all queries
- [ ] **Authentication**: JWT tokens, refresh tokens
- [ ] **Authorization**: Role-based + resource-based permissions
- [ ] **Relationships**: One-to-many, many-to-many
- [ ] **Nested Resources**: `/projects/{id}/tasks/{task_id}`
- [ ] **Filtering**: Complex queries across relationships
- [ ] **Pagination**: Cursor and offset pagination
- [ ] **Soft Deletes**: Archive vs. hard delete
- [ ] **Audit Trail**: Created by, updated by tracking
- [ ] **File Uploads**: Attachment handling
- [ ] **Webhooks**: Event notifications
- [ ] **Rate Limiting**: API throttling
- [ ] **React-Admin**: Full admin interface compatibility

### Phased Implementation

**Phase 1: Core Domain**
- Organization, User, Project, Task models
- Basic CRUD for all entities
- Relationship handling

**Phase 2: Authentication**
- User registration/login
- JWT access + refresh tokens
- Password hashing with bcrypt

**Phase 3: Permissions**
- Role-based access control
- Resource-level permissions
- Permission decorators

**Phase 4: React-Admin**
- Response format compatibility
- Bulk operations
- Reference fields
- Admin endpoints

**Phase 5: Production Features**
- Soft deletes
- Audit trail
- File uploads
- Webhooks

---

## Feature Roadmap

### Phase 1: Foundation Fixes ✅

**Outcome**: PATCH-based updates, session naming improvements, parity and stability fixes.

1. **Fix Sync/Async Parity**
   ```python
   # Both should work identically:
   def process_index(self, query_params):  # Currently missing query_params
   async def process_index(self, query_params):
   ```

2. **Fix REST Semantics**
   - PUT = full replacement (requires all fields)
   - PATCH = partial update (optional fields)
   - Currently PUT acts like PATCH

3. **Fix db_lifespan()**
   - Or remove if not providing value

4. **Session Naming**
   - Consider: `fr.Session` → `fr.SyncSession` to avoid collision
   - Or: Document the import order requirement

### Phase 2: Example Projects ✅

**Outcome**: `shop`, `blog`, and `saas` examples are working and tested.

### Phase 3: 1.0 Release Readiness (Current)

**Goal**: Complete documentation and release gating work required for a confident 1.0.

1. Document all public endpoints
2. Improve Getting Started experience
3. Publish How-To guides for core features
4. Keep CI green on Python 3.10-3.13 across framework + examples

### Phase 4: Authentication System (Post-1.0)

**Goal**: First-class authentication support without being opinionated about strategy.

```python
# Design: Composable authentication backends
from fastapi_restly.auth import JWTAuth, SessionAuth, APIKeyAuth

# Configuration
fr.configure_auth(
    backend=JWTAuth(
        secret="...",
        algorithm="HS256",
        access_token_expire=timedelta(minutes=15),
        refresh_token_expire=timedelta(days=7),
    )
)

# Usage in views
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    authentication = [JWTAuth]  # Require auth for all endpoints

    # Or per-endpoint
    @fr.get("/me")
    @fr.authenticated
    async def me(self, user: CurrentUser):
        return user
```

**Components**:
- `auth/backends/` - JWT, Session, API Key implementations
- `auth/schemas.py` - Token request/response schemas
- `auth/dependencies.py` - FastAPI dependencies (`CurrentUser`, `CurrentUserOptional`)
- `auth/views.py` - Pre-built login/logout/refresh views

**Token Flow**:
```
POST /auth/login     → {access_token, refresh_token}
POST /auth/refresh   → {access_token}
POST /auth/logout    → Invalidate refresh token
GET  /auth/me        → Current user info
```

### Phase 3: Permissions System

**Goal**: Flexible, composable permission system.

```python
# Design: Permission classes (inspired by DRF)
from fastapi_restly.permissions import (
    Permission,
    IsAuthenticated,
    IsAdmin,
    IsOwner,
    HasRole,
)

# Simple role check
@fr.include_view(app)
class AdminView(fr.AsyncAlchemyView):
    permissions = [IsAuthenticated, IsAdmin]

# Resource ownership
class TaskView(fr.AsyncAlchemyView):
    permissions = [IsAuthenticated]

    # Object-level permission
    def get_object_permissions(self, obj):
        return [IsOwner(owner_field="created_by")]

# Custom permission
class CanEditTask(Permission):
    def has_permission(self, request, view) -> bool:
        return request.user.is_staff

    def has_object_permission(self, request, view, obj) -> bool:
        return obj.project.has_member(request.user)
```

**Components**:
- `permissions/base.py` - `Permission` base class
- `permissions/common.py` - `IsAuthenticated`, `IsAdmin`, etc.
- `permissions/decorators.py` - `@requires_permission()`
- `permissions/mixins.py` - `PermissionMixin` for views

### Phase 4: React-Admin Compatibility

**Goal**: Zero-configuration react-admin compatibility.

React-admin expects:
1. **List Response Format**:
   ```json
   {
     "data": [...],
     "total": 100
   }
   ```
   With `Content-Range` header: `items 0-24/100`

2. **Single Item Response**:
   ```json
   {"id": 1, "name": "..."}
   ```

3. **Filtering via Query Params**:
   ```
   GET /users?filter={"name":"John"}&sort=["name","ASC"]&range=[0,24]
   ```

4. **Bulk Operations**:
   ```
   DELETE /users?filter={"id":[1,2,3]}
   ```

5. **Reference Fields**:
   - Return IDs for relationships
   - Support `_expand` or similar for eager loading

**Implementation**:

```python
# Option 1: Query modifier version for react-admin
fr.set_query_modifier_version(QueryModifierVersion.REACT_ADMIN)

# Option 2: Mixin
class UserView(fr.AsyncAlchemyView, fr.ReactAdminMixin):
    ...

# Option 3: Response transformer (middleware)
app = FastAPI()
app.add_middleware(ReactAdminMiddleware)
```

**Components**:
- `react_admin/query.py` - React-admin query parameter parsing
- `react_admin/response.py` - Response formatting
- `react_admin/middleware.py` - Optional middleware approach
- `react_admin/views.py` - `ReactAdminView` base class

### Phase 5: User Management

**Goal**: Complete user management out of the box.

```python
# Pre-built user management
from fastapi_restly.users import (
    User,                    # SQLAlchemy model with common fields
    UserView,                # Pre-built CRUD view
    UserSchema,              # Pydantic schema
    create_user_router,      # Factory for customization
)

# Simple usage
app.include_router(UserView.as_router())

# With customization
user_router = create_user_router(
    user_model=MyUser,
    user_schema=MyUserSchema,
    require_email_verification=True,
    password_validators=[MinLength(8), HasNumber(), HasSpecial()],
)
```

**Features**:
- Registration with email verification (optional)
- Password reset flow
- Account deactivation
- Profile management
- Email change verification

---

## API Surface Review

### Current Exports (from `__init__.py`)

```python
# Database
setup_async_database_connection, setup_database_connection
AsyncSession, Session, AsyncSessionDep, SessionDep
activate_savepoint_only_mode, deactivate_savepoint_only_mode

# Models
Base, IDBase, IDStampsBase, TimestampsMixin, IDMixin, mapped_column

# Query
QueryModifierVersion, apply_query_modifiers, create_query_param_schema
get_query_modifier_version, set_query_modifier_version

# Schemas
ReadOnly, WriteOnly, BaseSchema, IDSchema, IDStampsSchema
OmitReadOnlyMixin, PatchMixin
create_schema_from_model, auto_generate_schema_for_view
get_writable_inputs, resolve_ids_to_sqlalchemy_objects
async_resolve_ids_to_sqlalchemy_objects

# Views
include_view, AsyncAlchemyView, AlchemyView, View
route, get, post, put, delete
```

### Proposed API Surface (After Features)

```python
import fastapi_restly as fr

# === Core (unchanged) ===
fr.AsyncAlchemyView, fr.AlchemyView, fr.View
fr.include_view
fr.get, fr.post, fr.put, fr.patch, fr.delete  # Add patch

# === Database ===
fr.setup_async_database, fr.setup_database  # Renamed for clarity
fr.AsyncSessionDep, fr.SessionDep

# === Models ===
fr.Base, fr.IDBase, fr.IDStampsBase
fr.TimestampsMixin, fr.IDMixin

# === Schemas ===
fr.ReadOnly, fr.WriteOnly
fr.BaseSchema, fr.IDSchema

# === Query ===
fr.QueryVersion  # Renamed from QueryModifierVersion
fr.set_query_version, fr.get_query_version

# === Auth (new) ===
fr.auth.JWTAuth, fr.auth.SessionAuth, fr.auth.APIKeyAuth
fr.auth.CurrentUser, fr.auth.CurrentUserOptional
fr.auth.authenticated, fr.auth.login_required
fr.auth.configure

# === Permissions (new) ===
fr.permissions.Permission
fr.permissions.IsAuthenticated, fr.permissions.IsAdmin
fr.permissions.IsOwner, fr.permissions.HasRole

# === React-Admin (new) ===
fr.react_admin.ReactAdminView
fr.react_admin.configure

# === Users (new) ===
fr.users.User, fr.users.UserView
fr.users.create_user_router
```

---

## Development Principles

### Before Adding Any Feature

Ask these questions:

1. **Does this belong in the framework?**
   - Is it needed by >50% of users?
   - Can it be a separate package?

2. **Is the API symmetric?**
   - If async version exists, does sync exist?
   - If read version exists, does write exist?

3. **Is there one obvious way?**
   - Is the recommended approach clear?
   - Are alternatives documented?

4. **Is it composable?**
   - Can it be used independently?
   - Does it play well with other features?

5. **Is it tested?**
   - Unit tests for logic
   - Integration tests for views
   - Example project validates real usage

### Review Checklist

For every PR:

- [ ] Type annotations complete
- [ ] Docstrings on public API
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] Async/sync parity maintained
- [ ] No breaking changes (or documented)
- [ ] Example project still works

---

## Implementation Order

```
Phase 1: Foundation Fixes ✅
├── 1.1 Session naming updates
├── 1.2 Sync/async parity fixes
├── 1.3 PATCH semantics for updates
└── 1.4 Lifespan and stability fixes

Phase 2: Example Projects ✅
├── 2.1 Shop example
├── 2.2 Blog example
├── 2.3 Multi-tenant SaaS example
└── 2.4 Cross-example testing gate

Phase 3: 1.0 Release Readiness (CURRENT)
├── 3.1 Public endpoint API documentation
├── 3.2 Getting Started documentation
├── 3.3 Feature How-To guides
└── 3.4 Release checklist and RC validation

Phase 4+: Post-1.0 Expansion
├── 4.1 Authentication backends
├── 4.2 Permissions framework
├── 4.3 Admin/reaction-admin compatibility
└── 4.4 Additional ecosystem tooling
```

---

## Success Metrics

The framework is successful when:

1. **Zero to CRUD in 5 minutes**: New users can have working API in 5 minutes
2. **Examples are healthy**: Shop/Blog/SaaS all pass in CI
3. **Documentation is complete**: API reference + onboarding + how-to guides
4. **Release gating is strict**: Python 3.10-3.13 matrix stays green
5. **Tests pass**: Framework and example suites pass on every commit

---

## Decisions Made

| Question | Decision |
|----------|----------|
| **Session naming** | Prefix with `FR`: `FRSession`, `FRAsyncSession` |
| **PUT/PATCH** | Use `PATCH` for partial updates (REST-correct) |
| **Query version default** | Whatever react-admin uses becomes default (likely V3) |
| **Phase order** | React-Admin is Phase 3 (before auth/permissions) |

## Open Questions

1. **Auth backend**: Should JWT be default or should we be backend-agnostic?

2. **User model**: Should we provide a `User` model or just mixins?

3. **Soft deletes**: Framework feature or example pattern?

---

*This is a living document. Update as decisions are made and features are implemented.*
