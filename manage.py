#!/usr/bin/env python
"""Django management entry point."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datadesk.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django is not installed. Run 'make setup' to create the virtualenv "
            "and install requirements."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
