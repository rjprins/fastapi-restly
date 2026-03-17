FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for Claude Code)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# Install Claude Code globally
RUN npm install -g @anthropic-ai/claude-code

# Install uv and common Python dev tools
RUN pip install uv pytest pytest-asyncio httpx

# Note: Project files are mounted at runtime via docker-run.sh
# Install dependencies on first run with: uv pip install --system -e ".[dev]"

WORKDIR /app

CMD ["bash"]
