FROM python:3.12.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
RUN pip install --no-cache-dir psycopg[binary]==3.2.6 pika==1.3.2 jsonschema==4.23.0 pytest==8.3.5 Pillow==11.1.0 fastapi==0.115.11 uvicorn==0.34.0 httpx==0.28.1 ruff==0.11.0 mypy==1.15.0
COPY v2 /app/v2
COPY contracts /app/contracts
COPY tests/fixtures/v2 /app/tests/fixtures/v2
COPY infra/v2/compose.yaml /app/infra/v2/compose.yaml
COPY scripts/v2/backfill.py /app/scripts/v2/backfill.py
RUN useradd --uid 10001 --create-home lab
USER 10001
CMD ["python", "-m", "v2.foundation.cli", "health"]
