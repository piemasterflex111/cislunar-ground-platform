FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 ground \
    && useradd --uid 10001 --gid ground --create-home ground \
    && mkdir -p /var/lib/ground/vendor \
    && chown -R 10001:10001 /var/lib/ground
WORKDIR /app

COPY pyproject.toml README.md /app/
COPY mission_ground /app/mission_ground
RUN chmod -R a+rX /app \
    && pip install --no-cache-dir .

USER 10001:10001
CMD ["uvicorn", "mission_ground.services.ground_api:app", "--host", "0.0.0.0", "--port", "8080"]
