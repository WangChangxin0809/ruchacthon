# UNVERIFIED with the Claude-Code-only engine (docs/decisions/0003): the
# container needs a `claude` binary and a login. This installs the CLI; the
# login must come from a mounted ~/.claude (see ../docker-compose.yml).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY . .
EXPOSE 8787
CMD ["python3", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8787"]
