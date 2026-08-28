"""Gazetteer build requests (SCOPE.md §2.5).

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


class VocabularyTerm(models.Model):
    """One word a field is allowed to hold.

    A vocabulary is a list somebody maintains, not a constant somebody
    edits: a new kind of publication is a Tuesday, and shipping a deploy
    for it means the word waits for one. So the words live here and the
    page at /review/schema/ is where they are added.

    Seeded from the spellings the corpus already uses
    (`datasets/publishers.py`), which is also where the fallback lives:
    with no rows at all, reading a vocabulary answers from the seed rather
    than saying every value is wrong.

    `spelling` is the form the queue proposes for the rest. Held on the
    term rather than derived from a count, so a run of records written
    badly cannot make the bad spelling canonical.

    Retired rather than deleted. A word that is no longer offered is still
    on records written while it was, and deleting it turns a filter that
    matched them into one that matches nothing.
    """

    vocabulary = models.CharField(max_length=60, db_index=True)
    #: What is written on the record. Folded on save, so the same word
    #: cannot be added twice in two cases.
    value = models.CharField(max_length=120)
    label = models.CharField(max_length=120, blank=True, default="")
    spelling = models.CharField(max_length=120, blank=True, default="")
    retired = models.BooleanField(default=False)
    note = models.TextField(blank=True, default="")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["vocabulary", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["vocabulary", "value"], name="one_term_per_vocabulary"
            )
        ]

    def __str__(self):
        return f"{self.vocabulary}: {self.value}"
