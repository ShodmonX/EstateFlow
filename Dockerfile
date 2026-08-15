FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
COPY apps /app/apps

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN useradd --create-home --shell /usr/sbin/nologin estateflow

RUN mkdir -p /app/data/sessions \
    && chown -R estateflow:estateflow /app/data

USER estateflow

EXPOSE 8000

CMD ["uvicorn", "estateflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
