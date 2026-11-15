# Earth Analog Explorer API (Django) for Cloud Run.
# Build from the REPOSITORY ROOT (the image also needs the exported data in frontend/public/data):
#   docker build -f backend/Dockerfile -t analog-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DJANGO_DEBUG=0 \
    ANALOG_EXPORT_DIR=/app/data/export

WORKDIR /app/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# backend/ includes src/analog (the pipeline's scoring code: numpy + pandas only).
COPY backend/ .
COPY frontend/public/data /app/data/export

RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --uid 10001 --no-create-home app \
    && chmod +x entrypoint.sh
USER app

EXPOSE 8080
ENTRYPOINT ["./entrypoint.sh"]
