FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

# Dev images (docker compose) also get pytest; production images do not.
ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt /tmp/
RUN pip install -r /tmp/requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install -r /tmp/requirements-dev.txt; fi \
    && rm /tmp/requirements*.txt

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --chmod=755 scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chown=app:app src/ /app/

USER app
EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30"]
