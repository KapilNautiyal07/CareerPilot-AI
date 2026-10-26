FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 careerpilot && mkdir -p /app/var/uploads && chown -R careerpilot /app/var
USER careerpilot

ENV PORT=8000
EXPOSE 8000
# Apply migrations, then serve on the platform-provided $PORT. Proxy headers are trusted so the
# real client IP (forwarded by Vercel/Render) is used for rate limiting. One worker: background
# workflows run in-process.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
