FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system -e .
EXPOSE 8000
CMD ["uvicorn", "causaldeploy.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
