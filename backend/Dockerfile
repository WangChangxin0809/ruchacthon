# Built from the *repository root* as build context (see ../docker-compose.yml:
# `context: .., dockerfile: backend/Dockerfile`), not from backend/ alone --
# app/fs_tools.py finds the project root by walking up three parents from its
# own file, the same three hops whether it's running from a laptop checkout
# or in here, and that only lines up if /app mirrors the repo root exactly.
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY . .

EXPOSE 8787

CMD ["python3", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8787"]
