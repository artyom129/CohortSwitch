FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system cohortswitch && adduser --system --ingroup cohortswitch cohortswitch

COPY pyproject.toml README.md LICENSE ./
COPY backend ./backend
RUN pip install --upgrade pip && pip install .

USER cohortswitch
EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

