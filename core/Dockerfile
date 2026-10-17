FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv pip install --system -e .
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn causaldeploy.api.main:app --host 0.0.0.0 --port 8000"]
