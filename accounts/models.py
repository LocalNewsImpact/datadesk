"""Who may do what, where (ROADMAP item 1).

One row per (person, application, scope). The suite shares one set of
users — item 12 saw to that — so what someone may do has to be granted
per application: the same person can be an editor in Datadesk and a
reviewer in the Source Directory.

**Scope is a slug, not a foreign key.** Datasets live in the crawler's
database, reached through unmanaged models, and Django has no
cross-database foreign keys. `GazetteerBuildRequest.dataset_slug` already
takes the same approach. An empty scope means the whole application
rather than one dataset within it, which is also how an application with
no datasets is expressed — the directory has collections, a later one may
have neither.

**Global admin is `is_superuser`.** Item 1's model calls it
`User.is_admin`; rather than add a second global flag beside Django's,
`is_superuser` carries it. A superuser holds every privilege in every
application at every scope, and needs no rows here.
"""

from django.conf import settings
from django.db import models

from accounts.privileges import ROLE_CHOICES

#: Applications a grant can name. These match SERVICE_ROLE, which selects
#: the front end a process serves, so a grant and a deployment agree on
#: what an application is called.
DATADESK = "datadesk"
SOURCES = "sources"

APP_CHOICES = [
    (DATADESK, "Datadesk"),
    (SOURCES, "Source Directory"),
]

#: An empty scope means the whole application. Stored as "" rather than
#: NULL so the unique constraint below actually fires: in Postgres two
#: rows with NULL in a unique column do not collide, which would let one
#: person hold two application-wide roles at once.
WHOLE_APPLICATION = ""


class Grant(models.Model):
    """One person's role in one application, over one scope."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="grants",
    )
    app = models.CharField(max_length=32, choices=APP_CHOICES)
    scope = models.SlugField(
        max_length=100,
        blank=True,
        default=WHOLE_APPLICATION,
        help_text="A dataset slug, or blank for the whole application.",
    )
    role = models.CharField(max_length=32, choices=ROLE_CHOICES)

    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            # One role per person per scope. Without this a person could
            # hold viewer and editor on the same dataset, and every check
            # would need a precedence rule -- which is what the three
            # global groups needed, and what this model exists to retire.
            models.UniqueConstraint(
                fields=["user", "app", "scope"],
                name="one_role_per_user_app_scope",
            )
        ]
        indexes = [
            # Every check starts from the signed-in person and the running
            # application, then asks about a scope.
            models.Index(fields=["user", "app"], name="grant_user_app_idx"),
        ]
        ordering = ["app", "scope", "role"]

    def __str__(self):
        where = self.scope or "all"
        return f"{self.user} — {self.role} on {self.app}/{where}"

    @property
    def is_application_wide(self):
        return self.scope == WHOLE_APPLICATION
