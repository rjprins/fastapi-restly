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

## Current Phase: Foundation Fixes

**Epic:** `fastapi-restly-4m4` - Framework Design & Roadmap Execution

### Phase 1 Blockers (must complete first)
| ID | Priority | Issue |
|----|----------|-------|
| `fastapi-restly-jop` | P0 | Fix sync index() not passing query_params |
| `fastapi-restly-tgm` | P0 | Fix broken db_lifespan |
| `fastapi-restly-3p8` | P1 | Add read-only filtering to sync make_new_object |
| `fastapi-restly-edu` | P2 | Rename Session → FRSession, FRAsyncSession |
| `fastapi-restly-3lh` | P2 | Change PUT → PATCH |

### Phase Roadmap
```
Phase 1: Foundation Fixes (current) → Phase 2: Example Project
    → Phase 3: React-Admin → Phase 4: Auth → Phase 5: Permissions → Phase 6: Users
```

## Ralph Loop Instructions

### Before Each Iteration

1. **Check available work:**
   ```bash
   bd ready
   ```

2. **Claim a task:**
   ```bash
   bd update <id> --status=in_progress
   ```

3. **Refine if needed:** If a task is too vague or large, break it down:
   ```bash
   bd create --title="Subtask 1" --type=task --priority=1
   bd create --title="Subtask 2" --type=task --priority=1
   bd dep add <parent> <subtask1>
   bd close <parent> --reason="Split into subtasks"
   ```

### During Each Iteration

1. **Read the task:** `bd show <id>`
2. **Understand context:** Read relevant source files
3. **Make changes:** Edit code, add tests
4. **Verify:** Run `make test-framework` or specific tests
5. **Commit:** Stage and commit changes

### After Completing Work

1. **Close completed issues:**
   ```bash
   bd close <id1> <id2> ...
   ```

2. **Check for newly unblocked work:**
   ```bash
   bd ready
   ```

3. **Sync:**
   ```bash
   bd sync
   ```

### Completion Promise

When all Phase 1 blockers are resolved and `fastapi-restly-j1g` can be closed:

```
<promise>PHASE 1 COMPLETE</promise>
```

## Quality Gates

Before closing any issue:

- [ ] Code compiles/runs without errors
- [ ] Tests pass: `make test-framework`
- [ ] Async/sync parity maintained (if applicable)
- [ ] No regressions in existing tests

## Key Decisions (from PLAN.md)

| Decision | Value |
|----------|-------|
| Session naming | `FRSession`, `FRAsyncSession` |
| Update semantics | PATCH (not PUT) |
| Query default | React-admin format |

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

Phase 1 is complete when:
- All P0 bugs fixed
- Sync/async parity achieved
- Session classes renamed to FRSession/FRAsyncSession
- PUT changed to PATCH
- db_lifespan fixed or removed
- All tests pass
- `fastapi-restly-j1g` closed

---

*This PRD is consumed by Ralph Loop for autonomous development. Keep it updated as decisions are made.*
