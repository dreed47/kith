# The site in a container: gunicorn on port 8000. Photos and documents live in /data/media, the site's own files
# (secret key, uploads waiting to import, avatars) in /data/instance; mount both. Built by docker/compose.yaml.
FROM python:3.13-slim
# pdftoppm makes PDF thumbnails (their first page).
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY deploy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0
COPY app app
COPY themes themes
COPY pyproject.toml .
# Readable whatever the checkout's permissions; owned by root, so the site can't change its own code.
RUN chmod -R a+rX /app && useradd --system --home /app kith && mkdir -p /data/media /data/instance && chown kith /data/media /data/instance
USER kith
ENV KITH_MEDIA_ROOT=/data/media KITH_INSTANCE=/data/instance KITH_STRONG_PASSWORDS=1
EXPOSE 8000
# Database upgrades first, so a new version's tables are in place before it serves a page.
CMD ["sh", "-c", "flask --app app setup upgrade && exec gunicorn --workers 3 --bind 0.0.0.0:8000 --access-logfile - 'app:create_app()'"]
