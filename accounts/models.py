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

from accounts.privileges import ADMIN, DESIGNER, ROLE_CHOICES

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

#: Reference data that is nobody's corpus — FIPS codes, census tables,
#: demographics. It is a dataset like any other, and every account reads
#: and designs against it (ROADMAP item 1), so access is a rule rather
#: than a row per person: there is nothing to create at sign-up and
#: nothing that can be revoked by accident.
#:
#: Making it a dataset rather than a special case is what keeps scoping
#: uniform. A visual wired only to census tables is wired to *this*
#: dataset, not to none — so the empty set keeps meaning "no access"
#: everywhere, instead of meaning "unconstrained" in one place and its
#: opposite in another.
UNIVERSAL = "universal"


class Invitation(models.Model):
    """One address admitted from outside the organisation.

    Sign-in is otherwise closed to a hosted domain, which is the whole
    wall: a personal Google account carries no `hd` claim at all, so
    there is nothing for the domain check to accept and no consent screen
    can change that. This is the list of exceptions, by address, and
    somebody not on it is refused at the door exactly as before.

    A dataset is required. An invitation admits somebody *to work on
    something*, and one that named no dataset would admit them to a
    console with nothing in it -- which reads as a broken sign-in rather
    than as a grant nobody made. Designer is the default because it is
    the role this is for: reads and authors visuals, and decides no
    dispositions.

    The grant is made when they first sign in, not now, because a grant
    needs a user and a user does not exist until Google has said who they
    are. Until then this row is the whole record of the decision.
    """

    email = models.EmailField(unique=True)
    app = models.CharField(max_length=32, choices=APP_CHOICES, default=DATADESK)
    #: A dataset slug. Not blank: see the class docstring.
    scope = models.SlugField(max_length=100)
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default=DESIGNER)

    invited_at = models.DateTimeField(auto_now_add=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    #: When they first signed in and the grant was made. An invitation
    #: keeps admitting them afterwards -- revoking is deleting the row,
    #: and a used invitation is still how they get back in tomorrow.
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_invitation"
        ordering = ("email",)

    def __str__(self):
        return f"{self.email} → {self.role} on {self.scope}"

    @classmethod
    def for_email(cls, email):
        """The invitation for an address, matched the way people write
        one: case does not make a different person."""
        return cls.objects.filter(email__iexact=(email or "").strip()).first()


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
            ),
            # Admin is application-level by definition: full access to
            # everything in the application. An admin grant naming a single
            # dataset is a contradiction -- it reads as "full access to
            # everything, but only here" -- and would quietly behave like an
            # editor. Editor is the dataset-level role, and the one to use
            # when someone should own one dataset and not the rest.
            models.CheckConstraint(
                condition=~models.Q(role=ADMIN) | models.Q(scope=WHOLE_APPLICATION),
                name="admin_grants_are_application_wide",
            ),
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
