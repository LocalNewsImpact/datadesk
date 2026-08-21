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
"""

import os
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
    "explorer",
    "visuals",
]

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

ROOT_URLCONF = "datadesk.urls"

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
                "accounts.context_processors.role",
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
            "OPTIONS": {"connect_timeout": 10},
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

DATABASE_ROUTERS = ["explorer.routers.CrawlerRouter"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- authentication (SCOPE.md §2.1) -----------------------------------------
#
# Google via django-allauth is the sole sign-in path. Local password signup
# is closed (accounts.adapters.NoPublicSignupAdapter); the hosted-domain
# claim is verified in accounts.adapters.DomainRestrictedAdapter. The shape
# follows the production configuration of sources.localnewsimpact.org.

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    # ModelBackend remains for superuser login on the admin form;
    # no user-facing password path exists.
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
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
