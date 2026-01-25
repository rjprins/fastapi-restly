# FastAPI-Restly: Product Requirements Document

> For use with Ralph Loop autonomous development

## Overview

FastAPI-Restly is a REST framework for building maintainable CRUD APIs on top of FastAPI, SQLAlchemy 2.0, and Pydantic v2. This PRD guides autonomous development using the Ralph Loop methodology.

## Project State

### Strategic Plan
See `PLAN.md` for:
- Design philosophy (symmetry, progressive disclosure, composability)
- Quality standards
- Phase roadmap
- Decisions made

### Work Tracking
All work is tracked in **beads** (`.beads/` directory):

```bash
bd ready           # Find available work (no blockers)
bd show <id>       # View issue details
bd blocked         # See dependency chains
bd stats           # Project health
```

## Current Phase: Example Project (Phase 2)

**Epic:** `fastapi-restly-4m4` - Framework Design & Roadmap Execution
**Task:** `fastapi-restly-4w8` - Phase 2: Example Project - Multi-Tenant SaaS

### Phase 1 ✅ COMPLETE
- FRSession/FRAsyncSession naming
- PUT → PATCH
- Sync/async parity
- db_lifespan removed

### Phase Roadmap
```
Phase 1: Foundation Fixes ✅ → Phase 2: Example Project (CURRENT)
    → Phase 3: React-Admin → Phase 4: Auth → Phase 5: Permissions → Phase 6: Users
```

## Phase 2 Goal

Build a **realistic multi-tenant project management API** that validates the framework handles real-world complexity. This example will:
- Prove the API is production-ready
- Serve as documentation/reference
- Expose any missing features before Phase 3

## Domain Model

```
example-projects/saas/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── models/
│   │   ├── __init__.py
│   │   ├── organization.py  # Tenant model
│   │   ├── user.py          # User with org membership
│   │   ├── project.py       # Projects belong to org
│   │   └── task.py          # Tasks belong to project
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── ...              # Pydantic schemas
│   └── views/
│       ├── __init__.py
│       └── ...              # View classes
├── tests/
│   └── ...
├── alembic/                  # Migrations
└── conftest.py
```

### Models to Implement

1. **Organization** (tenant)
   - id, name, slug, created_at

2. **User**
   - id, email, name, organization_id (FK)
   - role: owner | admin | member

3. **Project**
   - id, name, description, organization_id (FK)
   - status: active | archived

4. **Task**
   - id, title, description, project_id (FK)
   - status: todo | in_progress | done
   - assignee_id (FK to User, nullable)
   - priority: 1-4

### Relationships to Test
- Organization → Users (one-to-many)
- Organization → Projects (one-to-many)
- Project → Tasks (one-to-many)
- User → Tasks (one-to-many, assignee)

## Implementation Checklist

### Step 1: Project Structure
- [ ] Create `example-projects/saas/` directory structure
- [ ] Set up `pyproject.toml` or use root deps
- [ ] Create `conftest.py` with test fixtures

### Step 2: Models
- [ ] Organization model with IDStampsBase
- [ ] User model with organization FK and role enum
- [ ] Project model with organization FK and status enum
- [ ] Task model with project FK, assignee FK, priority

### Step 3: Schemas
- [ ] OrganizationSchema with IDSchema
- [ ] UserSchema with ReadOnly org relationship
- [ ] ProjectSchema with nested task count (optional)
- [ ] TaskSchema with assignee reference

### Step 4: Views
- [ ] OrganizationView - basic CRUD
- [ ] UserView - CRUD with org scoping
- [ ] ProjectView - CRUD with org scoping
- [ ] TaskView - CRUD with project scoping

### Step 5: Advanced Features
- [ ] Nested routes: `/projects/{id}/tasks`
- [ ] Filtering: `?status=active&assignee_id=5`
- [ ] Sorting: `?order_by=-priority,created_at`
- [ ] Pagination: `?page=1&page_size=20`

### Step 6: Tests
- [ ] CRUD tests for each model
- [ ] Relationship tests (create with FK)
- [ ] Filter/sort/paginate tests
- [ ] Edge cases (404, validation errors)

## Ralph Loop Instructions

### Before Each Iteration

1. **Check progress:** Review what exists in `example-projects/saas/`
2. **Identify next step:** Follow the checklist above in order
3. **Create subtasks if needed:**
   ```bash
   bd create --title="Create Organization model" --type=task --priority=1
   ```

### During Each Iteration

1. **Write code:** Models, schemas, views
2. **Write tests:** Every feature needs tests
3. **Run tests:** `uv run pytest example-projects/saas/tests/ -v`
4. **Fix issues:** Iterate until tests pass

### After Completing Work

1. **Commit:** Stage and commit changes
2. **Update beads:** Close completed subtasks
3. **Check progress:** Review checklist

### Completion Promise

When the example project is complete with:
- All 4 models implemented
- All 4 views working
- Tests passing for CRUD, relationships, filtering

```
<promise>PHASE 2 COMPLETE</promise>
```

## Quality Gates

Before marking Phase 2 complete:

- [ ] All models have proper relationships
- [ ] All views have working CRUD endpoints
- [ ] Filtering works on all relevant fields
- [ ] Sorting works on all sortable fields
- [ ] Pagination works on list endpoints
- [ ] Tests cover all endpoints
- [ ] Tests pass: `uv run pytest example-projects/saas/ -v`

## Key Decisions (from PLAN.md)

| Decision | Value |
|----------|-------|
| Session naming | `FRSession`, `FRAsyncSession` |
| Update semantics | PATCH (not PUT) |
| Query default | React-admin format (Phase 3) |

## File Structure

```
fastapi_restly/
├── views/          # Class-based views (async + sync)
├── schemas/        # Pydantic schema utilities
├── db/             # Database session management
├── models/         # SQLAlchemy base classes
├── query/          # Query parameter systems (V1, V2)
└── testing/        # Test utilities
```

## Commands Reference

```bash
# Development
make test-framework          # Run framework tests
make test-all                # Run all tests including examples
uv run pytest tests/file.py  # Run specific test file

# Beads
bd ready                     # Available work
bd show <id>                 # Issue details
bd update <id> --status=in_progress  # Claim work
bd close <id>                # Complete work
bd sync                      # Push changes

# Git
git status                   # Check changes
git add <files>              # Stage
git commit -m "..."          # Commit
git push                     # Push
```

## Task Refinement Guidelines

If a task from beads is:

1. **Too vague:** Add detail via `bd update <id> --description="..."`
2. **Too large:** Split into subtasks with dependencies
3. **Missing context:** Check `PLAN.md` and related source files
4. **Blocked unexpectedly:** Add dependency via `bd dep add`
5. **Obsolete:** Close with reason via `bd close <id> --reason="..."`

## Success Criteria

Phase 2 is complete when:
- All 4 models (Organization, User, Project, Task) implemented
- All 4 views with full CRUD working
- Relationships properly handled (FK creation, nested responses)
- Query modifiers working (filter, sort, paginate)
- Comprehensive test suite passing
- Example serves as documentation for framework usage
- `fastapi-restly-4w8` closed

---

*This PRD is consumed by Ralph Loop for autonomous development. Keep it updated as decisions are made.*
