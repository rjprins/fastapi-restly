.PHONY: test test-framework test-examples test-all clean install-dev docs serve-docs

# Default target
all: test-all

# Install development dependencies
install-dev:
	uv sync
	cd example-projects/shop && uv sync --active
	cd example-projects/blog && uv sync --active

# Test the main framework
test-framework:
	@echo "=== Testing FastAPI-Restly Framework ==="
	uv run pytest tests/ -v

# Test shop example
test-shop:
	@echo "=== Testing Shop Example ==="
	cd example-projects/shop && uv run --active pytest tests/ -v

# Test blog example  
test-blog:
	@echo "=== Testing Blog Example ==="
	cd example-projects/blog && uv run --active pytest blog/ -v

# Test all examples
test-examples: test-shop test-blog

# Test everything
test-all: test-framework test-examples
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
	uv run pytest tests/ --cov=fastapi_restly --cov-report=term-missing
	cd example-projects/shop && uv run --active pytest tests/ --cov=shop --cov-report=term-missing
	cd example-projects/blog && uv run --active pytest blog/ --cov=blog --cov-report=term-missing

docs:
	uv run sphinx-build -M html docs site
	@echo "Documentation available at site/index.html"

serve-docs:
	uv run sphinx-autobuild docs site

# Help
help:
	@echo "Available commands:"
	@echo "  test-framework  - Test the main FastAPI-Restly framework"
	@echo "  test-shop       - Test the shop example"
	@echo "  test-blog       - Test the blog example"
	@echo "  test-examples   - Test all examples"
	@echo "  test-all        - Test framework and all examples"
	@echo "  test            - Quick test (just framework)"
	@echo "  test-coverage   - Run tests with coverage reports"
	@echo "  install-dev     - Install all development dependencies"
	@echo "  clean           - Clean up cache and temporary files"
	@echo "  ci              - Full CI setup and test run"
	@echo "  dev-setup       - Setup development environment"
	@echo "  docs            - Build documentation" 
	@echo "  serve-docs      - Autobuild and serve documentation" 
