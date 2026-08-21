"""WSGI entry point for Datadesk."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datadesk.settings")

application = get_wsgi_application()
