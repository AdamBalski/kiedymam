FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATABASE_PATH=/data/umb-syncer.sqlite3
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --create-home app \
    && mkdir -p /data /app/app/static \
    && chown -R app:app /data \
    && chmod 700 /data
COPY backend/app/ ./app/
COPY --from=frontend-build /frontend/dist/ ./app/static/
USER app
EXPOSE 8080
VOLUME ["/data"]
CMD ["sh", "-c", "umask 077 && uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT}"]
