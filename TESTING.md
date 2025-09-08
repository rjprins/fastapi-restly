# Testing FastAPI-Restly

This document explains how to run tests for the FastAPI-Restly framework and its example projects.

## Quick Start

### Using make
```bash
# Run all tests (framework + examples)
make test-all

# Run only framework tests
make test-framework

# Run only example tests
make test-examples

# Run specific example
make test-shop
make test-blog

# Install dependencies and run all tests
make ci
```

## Using pytest

### Framework Tests
```bash
# From project root
uv run pytest tests/ -v
```

### Shop Example Tests
```bash
# From project root
cd example-projects/shop
uv run pytest tests/ -v
```

### Blog Example Tests
```bash
# From project root
cd example-projects/blog
uv run pytest blog/ -v
```

## Test Structure

### Framework Tests (`tests/`)
- **`test_imports.py`** - Tests that all imports work correctly
- Tests module accessibility and basic functionality

### Shop Example Tests (`example-projects/shop/tests/`)
- **`test_main.py`** - Tests the shop API endpoints
- Tests OpenAPI spec generation
- Tests CRUD operations (create customer, etc.)

### Blog Example Tests (`example-projects/blog/blog/`)
- **`test_main.py`** - Tests the blog API endpoints
- Tests OpenAPI spec generation
- Tests basic blog operations


## Troubleshooting

### Common Issues

1. **Missing Dependencies**
   ```bash
   make install-dev
   ```

2. **Python Environment Issues**
   ```bash
   # Clean and reinstall
   make clean
   make install-dev
   ```

3. **Database Issues**
   ```bash
   # Reset database state
   make clean
   make test-all
   ```

## Test Configuration

### Pytest Configuration
- Located in `pyproject.toml`
- Uses `uv` for dependency management
- Supports async tests with `pytest-asyncio`

### Database Testing
- Uses SQLite for testing
- Implements savepoint-based test isolation
- Automatic Alembic migrations

### Coverage Reports
```bash
# Run with coverage
make test-coverage
```

## Contributing

When adrestly new features:

1. Add tests for the framework functionality
2. Update example projects if needed
3. Ensure all tests pass: `make test-all`
4. Consider adrestly integration tests for complex scenarios

## Test Commands Reference

| Command | Description |
|---------|-------------|
| `make test-all` | Run all tests |
| `make test-framework` | Run framework tests only |
| `make test-examples` | Run example tests only |
| `make test-shop` | Run shop example tests |
| `make test-blog` | Run blog example tests |
| `make test-coverage` | Run tests with coverage |
| `make install-dev` | Install all dependencies |
| `make clean` | Clean cache files |
| `make ci` | Full CI setup and test run |
| `./test.sh` | Run all tests (shell script) |
| `python3 run_tests.py` | Run all tests (Python script) | 
