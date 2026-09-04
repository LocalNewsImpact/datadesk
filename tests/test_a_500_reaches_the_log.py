"""A traceback in production has to reach the log.

Django's default configuration sends `django.request` errors to
mail_admins (filtered by require_debug_false) and to console (filtered by
require_debug_true). With DEBUG off and no admin mail configured, both
filters close and the traceback is discarded -- Cloud Logging recorded an
ERROR entry with an empty payload, and the only sign a page had failed
was a 500 in the access log.

Every defect in this repository's recent history took somebody opening a
page to find. This is why.
"""

import logging

from django.conf import settings
from django.test import RequestFactory


def test_request_errors_have_a_handler():
    """Not mail_admins, which is unconfigured, and not one filtered by
    DEBUG, which is off exactly where the traceback matters."""
    config = settings.LOGGING["loggers"]["django.request"]
    assert config["handlers"], "django.request has nowhere to write"
    for name in config["handlers"]:
        handler = settings.LOGGING["handlers"][name]
        assert (
            handler["class"] == "logging.StreamHandler"
        ), "Cloud Run collects stdout; a traceback anywhere else is lost"
        assert (
            "filters" not in handler
        ), f"handler {name} is filtered, which is how the traceback was lost"


def test_the_traceback_is_written_when_debug_is_off(caplog, settings):
    """The condition that matters: DEBUG off, an unhandled exception, and
    the traceback in the log anyway."""
    settings.DEBUG = False
    logger = logging.getLogger("django.request")
    with caplog.at_level(logging.ERROR, logger="django.request"):
        try:
            raise ValueError("could not identify an equality operator")
        except ValueError:
            logger.error(
                "Internal Server Error: /review/queue/",
                exc_info=True,
                extra={"status_code": 500, "request": RequestFactory().get("/")},
            )
    assert "could not identify an equality operator" in caplog.text
    assert "Traceback" in caplog.text, "the exception was logged without its traceback"


def test_queries_are_not_logged_at_debug():
    """A log charged by volume does not want every SELECT."""
    assert settings.LOGGING["loggers"]["django.db.backends"]["level"] != "DEBUG"
