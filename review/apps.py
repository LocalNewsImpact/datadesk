from django.apps import AppConfig


class ReviewConfig(AppConfig):
    name = "review"
    verbose_name = "Review and cleanup"

    def ready(self):
        # Queues register themselves at import (review/kernel.py), so a
        # queue nothing had imported did not exist -- `kernel.get` raised
        # for it, and anything asking the registry what queues there are
        # got whatever the request had happened to touch.
        #
        # Imported here so the registry is complete as soon as the app is,
        # which is what a registry has to promise.
        from review import dispositions  # noqa: F401
