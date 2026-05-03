FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY src ./src
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "agent.server:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
