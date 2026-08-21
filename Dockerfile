# The application layer: nothing but our own code on top of the dependency
# image.
#
# BASE_IMAGE is supplied by the deploy, pinned to a hash of requirements.txt,
# so a deploy that changes only application code builds in seconds and pushes
# a few megabytes rather than reinstalling every library.

ARG BASE_IMAGE=datadesk-base:local
FROM ${BASE_IMAGE}

WORKDIR /app

COPY manage.py ./
COPY datadesk/ ./datadesk/
COPY accounts/ ./accounts/
COPY audit/ ./audit/
COPY datasets/ ./datasets/
COPY explorer/ ./explorer/
COPY review/ ./review/
COPY visuals/ ./visuals/
COPY templates/ ./templates/
COPY static/ ./static/

# Static files are baked in and served by WhiteNoise; without this the admin
# renders unstyled on Cloud Run. The dummy secret never reaches the runtime
# environment — collectstatic only needs settings to import.
RUN DJANGO_SECRET_KEY=build-only \
    python manage.py collectstatic --noinput \
 && chown -R app:app /app

USER app
EXPOSE 8080

# One worker because Cloud Run bills per instance and handles concurrency
# itself; threads because console requests wait on the database; --timeout 0
# because Cloud Run enforces its own deadline and a second one only produces
# confusing 502s.
CMD exec gunicorn datadesk.wsgi:application \
    --bind :${PORT:-8080} \
    --workers 1 \
    --threads 8 \
    --timeout 0 \
    --access-logfile - \
    --error-logfile -
