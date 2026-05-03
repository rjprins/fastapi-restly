.PHONY: test test-framework test-typing test-examples test-all clean install-dev lint pre-commit-install pre-commit-run docs docs-serve docs-push build-pages

# Default target
all: test-all

# Install development dependencies
install-dev:
	uv sync
	cd example-projects/shop && uv sync --all-extras --all-groups
	cd example-projects/blog && uv sync --all-extras --all-groups
	cd example-projects/saas && uv sync --all-extras --all-groups

pre-commit-install:
	uv run pre-commit install

pre-commit-run:
	uv run pre-commit run --all-files

lint:
	uv run ruff check .

test-typing:
	@echo "=== Testing Typing Compatibility Fixtures ==="
	uv run --with pyright pyright -p tests/typing/pyrightconfig.json

# Test the main framework
test-framework:
	@echo "=== Testing FastAPI-Restly Framework ==="
	uv run pytest tests/ -v

# Test shop example
test-shop:
	@echo "=== Testing Shop Example ==="
	cd example-projects/shop && uv run pytest tests/ -v

# Test blog example
test-blog:
	@echo "=== Testing Blog Example ==="
	cd example-projects/blog && uv run pytest tests/ -v

# Test saas example
test-saas:
	@echo "=== Testing SaaS Example ==="
	cd example-projects/saas && uv run pytest tests/ -v

# Test all examples
test-examples: test-shop test-blog test-saas

# Test everything
test-all: test-framework test-typing test-examples
	@echo "=== All Tests Complete ==="

# Quick test (just framework)
test: test-framework

# Clean up cache and temporary files
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true

# Install all dependencies and run tests
ci: install-dev test-all

# Development helpers
dev-setup: install-dev
	@echo "Development environment ready!"

# Run tests with coverage
test-coverage:
	@echo "=== Running Tests with Coverage ==="
	uv run pytest tests/ --cov=fastapi_restly --cov-report=term-missing --cov-report=xml

docs:
	uv run sphinx-build -M html docs site
	@echo "Documentation available at site/index.html"

docs-serve:
	uv run sphinx-autobuild docs site

build-pages:
	rm -rf site/html htmlcov
	uv run pytest tests/ --cov=fastapi_restly --cov-report=term-missing --cov-report=xml --cov-report=json:coverage.json --cov-report=html
	uv run sphinx-build -M html docs site
	mkdir -p site/html/coverage
	cp -rf htmlcov/. site/html/coverage/
	uv run python scripts/render_coverage_badge.py coverage.json site/html/coverage/badge.svg site/html/coverage/summary.json

docs-push: build-pages
	uv run ghp-import --no-history --no-jekyll --push site/html

# Help
help:
	@echo "Available commands:"
	@echo "  test-framework  - Test the main FastAPI-Restly framework"
	@echo "  test-typing     - Run Pyright on consumer typing fixtures"
	@echo "  test-shop       - Test the shop example"
	@echo "  test-blog       - Test the blog example"
	@echo "  test-saas       - Test the SaaS example"
	@echo "  test-examples   - Test all examples"
	@echo "  test-all        - Test framework and all examples"
	@echo "  test            - Quick test (just framework)"
	@echo "  test-coverage   - Run tests with coverage reports"
	@echo "  install-dev     - Install all development dependencies"
	@echo "  lint            - Run Ruff lint checks"
	@echo "  pre-commit-install - Install pre-commit hooks"
	@echo "  pre-commit-run  - Run all pre-commit hooks"
	@echo "  clean           - Clean up cache and temporary files"
	@echo "  ci              - Full CI setup and test run"
	@echo "  dev-setup       - Setup development environment"
	@echo "  docs            - Build documentation"
	@echo "  serve-docs      - Autobuild and serve documentation"
	@echo "  build-pages     - Build docs plus coverage assets for GitHub Pages"
	@echo "  docs-push       - Publish docs plus coverage assets to gh-pages"
