FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app /logs/verifier \
    && chown -R appuser:appuser /app /logs \
    && chmod 0777 /logs/verifier

WORKDIR /app

COPY app/ /app/

RUN chown -R appuser:appuser /app \
    && chmod 0644 /app/route_model.py

USER appuser
CMD ["sleep", "infinity"]
