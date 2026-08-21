"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Serve static files without the hashed manifest during tests.

    Production uses WhiteNoise's manifest storage, which refuses to render
    a page referencing a file collectstatic has not processed. That is the
    right behaviour when deployed and useless in a test run, where
    rendering a page would otherwise fail on missing CSS rather than on
    anything real. (Pattern from NewsSourceDirectory tests/conftest.py.)
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
