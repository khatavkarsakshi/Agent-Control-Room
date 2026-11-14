FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /code

# Run as an unprivileged user; the application never needs root at runtime.
RUN addgroup --system app \
    && adduser --system --uid 1000 --ingroup app --home /home/app app \
    && mkdir -p /home/app \
    && chown app:app /home/app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
