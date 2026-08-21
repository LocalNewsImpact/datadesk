"""Gazetteer build requests (SCOPE.md §2.4).

The build itself is the crawler's offline job
(`populate-gazetteer --dataset <slug>`, the state-extract path — never
public Overpass); Datadesk queues the request, surfaces its status, and
prints the exact command until the dispatch wiring to the crawler's
cluster lands.
"""

from django.conf import settings
from django.db import models


class GazetteerBuildRequest(models.Model):
    REQUESTED = "requested"
    DONE = "done"
    STATUSES = [(s, s) for s in (REQUESTED, DONE)]

    dataset_slug = models.SlugField()
    state = models.CharField(max_length=2, blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUSES, default=REQUESTED)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.dataset_slug} ({self.status})"

    @property
    def command(self):
        return f"python -m src.cli populate-gazetteer --dataset {self.dataset_slug}"
