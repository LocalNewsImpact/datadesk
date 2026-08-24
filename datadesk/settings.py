"""Django settings for Datadesk.

Environment-driven configuration. All deployment-specific values come from
environment variables with development-safe defaults, so the same settings
module serves local development and the decided hosting (SCOPE.md §6:
Cloud Run, sources-directory pattern; a `datadesk` database on the shared
Cloud SQL instance). No deployment-specific value is encoded here.

Configuration shapes follow the two sibling systems:

- Auth: the django-allauth Google + hosted-domain pattern proven on
  sources.localnewsimpact.org (NewsSourceDirectory config/settings.py and
  directory/auth.py), generalized to a domain list via ALLOWED_AUTH_DOMAINS.
- Database seam: Cloud Run reaches Cloud SQL over a unix socket
  (/cloudsql/<connection-name>), credentials from Secret Manager — the
  sources-directory pattern (the deploy workflow passes the connection
  name and the service assembles the DSN). Activated by
  CLOUD_SQL_CONNECTION_NAME in the environment; sqlite otherwise.

Environment variables:

    DJANGO_SECRET_KEY           required in production; insecure default for dev
    DJANGO_DEBUG                "1"/"true" to enable; default off
    DJANGO_ALLOWED_HOSTS        comma-separated; default "localhost,127.0.0.1"
    SESSION_COOKIE_DOMAIN       parent domain to share the session across
                                (".localnewsimpact.org"); unset locally
    DJANGO_CSRF_TRUSTED_ORIGINS comma-separated; default empty
    ALLOWED_AUTH_DOMAINS        comma-separated Google hosted domains permitted
                                to sign in; empty means no domain restriction
                                (development only)
    GOOGLE_OAUTH_CLIENT_ID      Google OAuth client credentials; blank locally
    GOOGLE_OAUTH_CLIENT_SECRET  leaves the provider unconfigured
    DATADESK_SQLITE_PATH        development sqlite location override
    CLOUD_SQL_CONNECTION_NAME   presence switches to Postgres over the
                                /cloudsql unix socket (production)
    DB_NAME / DB_USER           default "datadesk"
    DB_PASSWORD                 from Secret Manager in production
    CRAWLER_DB_USER             presence configures the read-only crawler
                                alias (production: datadesk_ro)
    CRAWLER_DB_PASSWORD         from Secret Manager (crawler-ro-password)
    CRAWLER_DB_NAME / _HOST / _PORT   default mizzou / the shared socket
                                (or 127.0.0.1 for a local proxy) / 5432
    CRAWLER_RW_DB_USER          presence configures the audited write
                                alias (production: datadesk_rw)
    CRAWLER_RW_DB_PASSWORD      from Secret Manager (crawler-rw-password)
"""

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable."""
    return os.environ.get(name, str(int(default))).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    """Read a comma-separated environment variable as a list."""
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- core -------------------------------------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-real-secret")

DEBUG = env_bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Cloud Run terminates TLS at the edge and forwards plain HTTP to the
# container, so request.is_secure() is False unless Django is told to
# trust the forwarded protocol. Without this every absolute URL the app
# builds is http:// — including the OAuth callback allauth sends to
# Google, which then fails redirect_uri_mismatch against the registered
# https:// URI. Safe here because the only ingress is Cloud Run's proxy,
# which strips client-supplied X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Cookies ride TLS in production; local development stays plain HTTP.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# One sign-in across the suite (ROADMAP.md item 12).
#
# Datadesk owns identity; the Source Directory reads the same user and
# session tables. A cookie on the parent domain is what carries the
# session between the two subdomains — no load balancer, no shared
# origin. Unset locally, where there is no parent domain to share.
SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN", "") or None
CSRF_COOKIE_DOMAIN = SESSION_COOKIE_DOMAIN

# Renamed deliberately at the same time. A cookie's domain is part of its
# identity, so widening it leaves the old host-only `sessionid` in the
# browser alongside the new one, both are sent, and which wins is not
# something to leave to chance. A new name is a clean cut: everyone signs
# in once more, and after that the session is the suite's.
SESSION_COOKIE_NAME = "lnic_session"
CSRF_COOKIE_NAME = "lnic_csrf"


# Which front end this process serves (ROADMAP.md item 14). One image
# backs several services; the deployment says which one it is.
#
#   datadesk   the console at datadesk.localnewsimpact.org
#   sources    the Source Directory at sources.localnewsimpact.org
#
# The directory's app is loaded only for its own front end rather than
# unconditionally. It registers eleven models against the default
# AdminSite and patches admin.site.index at import, so loading it here
# would put its models and its dashboard on Datadesk's admin as well.
# That merge is wanted eventually — the suite shares one set of admins —
# but it needs the per-application grants from item 1 to filter what
# each person sees, and those do not exist yet.
SERVICE_ROLE = os.environ.get("SERVICE_ROLE", "datadesk").strip() or "datadesk"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # auth (SCOPE.md §2.1)
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    # Datadesk apps
    "accounts",
    "audit",
    "datasets",
    "explorer",
    "review",
    "visuals",
]

if SERVICE_ROLE == "sources":
    # First, and it has to be: this app overrides templates belonging to
    # django.contrib.admin and to allauth, and APP_DIRS takes the first
    # match walking INSTALLED_APPS.
    INSTALLED_APPS = ["directory", *INSTALLED_APPS, "import_export", "simple_history"]

# The Source Directory asks this rather than `is_staff`, so both consoles
# decide access from the same grants (ROADMAP item 1). Set only for the
# front end that serves it; unset, that package falls back to `is_staff`.
DIRECTORY_ADMIN_GATE = (
    "accounts.access.may_reach_sources_admin" if SERVICE_ROLE == "sources" else ""
)

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

if SERVICE_ROLE == "sources":
    MIDDLEWARE = [*MIDDLEWARE, "simple_history.middleware.HistoryRequestMiddleware"]

# Set where a reader looks for it rather than overridden later: both
# ROOT_URLCONF and LOGIN_REDIRECT_URL have defaults further down this
# file, and an earlier conditional assignment would be silently replaced
# by them.
ROOT_URLCONF = {
    "sources": "datadesk.urls_sources",
    # The public data front end (ROADMAP item 24): feeds, payloads and
    # embeds, and no console. Nothing of the console exists in that urlconf
    # rather than existing and refusing.
    "data": "datadesk.urls_data",
}.get(SERVICE_ROLE, "datadesk.urls")

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.access",
            ],
        },
    },
]

WSGI_APPLICATION = "datadesk.wsgi.application"

# --- databases --------------------------------------------------------------
#
# Development default: local sqlite. Production is the `datadesk` database
# on the shared Cloud SQL instance (application state only — SCOPE.md §1,
# placement decided in §6.2), reached the way the sources directory reaches
# it from Cloud Run: over the unix socket the platform mounts at
# /cloudsql/<connection-name>, password from Secret Manager
# (--auto-iam-authn alone fails on this instance). The deploy workflow
# passes the connection name and the service assembles the DSN itself —
# a DATABASE_URL built in CI would point at the runner's localhost.
#
# Read-only crawler-DB and BigQuery connections are configured separately
# (Phase 0 infrastructure work); they are not Django DATABASES entries.

if "CLOUD_SQL_CONNECTION_NAME" in os.environ:

    def _db_options():
        options = {"connect_timeout": 10}
        search_path = os.environ.get("DB_SEARCH_PATH", "").strip()
        if search_path:
            options["options"] = f"-c search_path={search_path}"
        return options

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "datadesk"),
            "USER": os.environ.get("DB_USER", "datadesk"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": f"/cloudsql/{os.environ['CLOUD_SQL_CONNECTION_NAME']}",
            "PORT": "",
            # The instance is shared three ways (crawler, sources
            # directory, datadesk) with one connection cap, so pooling is
            # deliberate rather than defaulted: hold a connection across
            # requests, but not forever.
            "CONN_MAX_AGE": 60,
            # A schema search path, where one is set. The Source
            # Directory's tables live in a `directory` schema of this
            # database and the shared identity tables in `public`, so
            # serving that front end means naming both: unqualified
            # names resolve to the directory's own first and fall
            # through to the shared ones. Unset for this console, where
            # everything is in `public`.
            "OPTIONS": _db_options(),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("DATADESK_SQLITE_PATH", BASE_DIR / "db.sqlite3"),
        }
    }

# The crawler's database (SCOPE.md §1: articles, enrichment, datasets,
# sources, gazetteer), read through the SELECT-only datadesk_ro role
# (infra/sql/create_crawler_readonly_role.sql). Postgres enforces read-only;
# explorer.routers.CrawlerRouter keeps Django from even trying to migrate
# or write here. Unset locally, the alias falls back to an (empty) sqlite
# file so the code paths exist and views degrade to "not connected".
if "CRAWLER_DB_USER" in os.environ:
    DATABASES["crawler"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("CRAWLER_DB_NAME", "mizzou"),
        "USER": os.environ["CRAWLER_DB_USER"],
        "PASSWORD": os.environ.get("CRAWLER_DB_PASSWORD", ""),
        # Same unix socket as the default database in production — one
        # instance carries both. Locally, the Cloud SQL Auth Proxy.
        "HOST": os.environ.get("CRAWLER_DB_HOST")
        or (
            f"/cloudsql/{os.environ['CLOUD_SQL_CONNECTION_NAME']}"
            if "CLOUD_SQL_CONNECTION_NAME" in os.environ
            else "127.0.0.1"
        ),
        "PORT": os.environ.get("CRAWLER_DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"connect_timeout": 10},
    }
else:
    DATABASES["crawler"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get(
            "DATADESK_CRAWLER_SQLITE_PATH", BASE_DIR / "crawler.sqlite3"
        ),
    }

# The audited write path (SCOPE.md §6.5): same database, the datadesk_rw
# role with column-level grants. Reads keep flowing through datadesk_ro;
# CrawlerRouter sends writes here when the alias exists. Locally unset,
# writes fall through to the (sqlite) crawler alias, which is how tests
# exercise the write path without Postgres.
if "CRAWLER_RW_DB_USER" in os.environ:
    DATABASES["crawler_rw"] = {
        **DATABASES["crawler"],
        "USER": os.environ["CRAWLER_RW_DB_USER"],
        "PASSWORD": os.environ.get("CRAWLER_RW_DB_PASSWORD", ""),
    }

DATABASE_ROUTERS = ["explorer.routers.CrawlerRouter"]

# A cache the whole service shares, not one per process.
#
# The dashboard already wraps its expensive reads in cache.get_or_set
# with a five-minute life. Without a CACHES setting Django falls back to
# LocMemCache, which lives inside a single worker process — and the
# service runs with min-instances 0, so most requests arrive at a cold
# container with an empty cache and pay the full cost again. The cache
# existed and could not work.
#
# The database table is deliberate rather than Redis: it is shared,
# costs nothing, needs no new infrastructure, and a lookup against it
# takes milliseconds against reads that take seconds. It lives in the
# application database — never the crawler's, which is read-only.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "datadesk_cache",
        "TIMEOUT": 300,
        "OPTIONS": {"MAX_ENTRIES": 1000},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- authentication (SCOPE.md §2.1) -----------------------------------------
#
# Google via django-allauth is the sole sign-in path. Local password signup
# is closed (accounts.adapters.NoPublicSignupAdapter); the hosted-domain
# claim is verified in accounts.adapters.DomainRestrictedAdapter. The shape
# follows the production configuration of sources.localnewsimpact.org.

# django_site is shared with the Source Directory, and a row cannot be.
# This console owns row 1; the directory owns row 2 and its deployment
# says so. The default keeps a plain checkout on the first row.
SITE_ID = int(os.environ.get("SITE_ID", "1"))

AUTHENTICATION_BACKENDS = [
    # ModelBackend remains for superuser login on the admin form;
    # no user-facing password path exists.
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/admin/" if SERVICE_ROLE == "sources" else "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_ADAPTER = "accounts.adapters.NoPublicSignupAdapter"
SOCIALACCOUNT_ADAPTER = "accounts.adapters.DomainRestrictedAdapter"

# Let a Google identity attach to an account that already exists with the
# same address, so an editor can be added before they have ever signed in.
# allauth disables this by default as an account-takeover vector; that risk
# does not apply here because DomainRestrictedAdapter refuses any login
# whose email is unverified or outside the allowed hosted domains.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

# Hosted domains allowed to sign in. `hd` below is a hint to Google's
# account chooser and is NOT enforcement — the claim is verified in
# accounts/adapters.py. Empty means unrestricted (development only).
ALLOWED_AUTH_DOMAINS = env_list("ALLOWED_AUTH_DOMAINS")

# .strip() matters: secrets may exist as blank placeholders until real
# credentials are issued, and whitespace would read as "configured".
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()


def build_socialaccount_providers(
    client_id: str, secret: str, hosted_domains: list[str]
) -> dict:
    """Describe the Google provider, and only describe an app when there is one.

    An APP entry with empty credentials is worse than none: allauth uses it,
    the token exchange fails, and the user sees "Third-Party Login Failure"
    with a bare 401 in the logs. Without it the provider is simply
    unconfigured, which says so plainly and lets a contributor run the
    project on the ordinary Django admin login.
    """
    config: dict = {
        "google": {
            "SCOPE": ["profile", "email"],
            # A hint to Google's account chooser, not enforcement; only
            # sendable when exactly one domain is allowed.
            "AUTH_PARAMS": (
                {"hd": hosted_domains[0]} if len(hosted_domains) == 1 else {}
            ),
        }
    }
    if client_id and secret:
        config["google"]["APP"] = {"client_id": client_id, "secret": secret, "key": ""}
    return config


SOCIALACCOUNT_PROVIDERS = build_socialaccount_providers(
    GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, ALLOWED_AUTH_DOMAINS
)

GOOGLE_SIGN_IN_CONFIGURED = bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)

# --- i18n / static ----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}


#: What CompressedManifestStaticFilesStorage writes: name.<12 hex>.ext
_HASHED_NAME = re.compile(r"\.[0-9a-f]{12}\.")


def _immutable_static(path, url):
    """Which static files may be cached for good.

    WhiteNoise's own answer is "the ones with a hash in the name", which
    is right and misses the largest file we serve. The map boundaries are
    fetched by the chart runtime, which builds their URLs itself --
    `{% static 'geo/' %}` plus a filename it picks from the geo level, and
    per-state files it picks from the data. A directory cannot be hashed,
    so none of them go through the manifest and all of them arrived with
    WhiteNoise's 60-second default. counties-10m.json is 822KB and was
    re-downloaded every minute, for every reader, on a page whose whole
    job is to load fast inside somebody else's article.

    They are treated as immutable instead, which obliges the filename to
    carry the vintage: a new boundary release is a new file, never an
    edit to this one. Anything already cached against the old name would
    otherwise stay cached for a year.
    """
    # Read through django.conf rather than the name above it: Django
    # normalises STATIC_URL to a leading slash on the way out, and the
    # raw value here is "static/", which matches no URL at all.
    from django.conf import settings as _s

    if url.startswith(f"{_s.STATIC_URL}geo/"):
        return True
    # The manifest's own naming, checked directly rather than by calling
    # WhiteNoise's version of it: that one is a method wanting a
    # configured instance, and this is installed unbound.
    return bool(_HASHED_NAME.search(url.rsplit("/", 1)[-1]))


WHITENOISE_IMMUTABLE_FILE_TEST = _immutable_static
