"""Django settings for Datadesk.

Environment-driven configuration. All deployment-specific values come from
environment variables with development-safe defaults, so the same settings
module serves local development and any of the hosting options left open in
SCOPE.md §6 (GKE vs the sources-directory pattern; shared vs dedicated
Cloud SQL). No hosting, domain, or database-placement decision is encoded
here.

Configuration shapes follow the two sibling systems:

- Auth: the django-allauth Google + hosted-domain pattern proven on
  sources.localnewsimpact.org (NewsSourceDirectory config/settings.py and
  directory/auth.py), generalized to a domain list via ALLOWED_AUTH_DOMAINS.
- Database seam: the MizzouNewsCrawler Cloud SQL env contract
  (USE_CLOUD_SQL_CONNECTOR / CLOUD_SQL_INSTANCE / DATABASE_USER /
  DATABASE_PASSWORD / DATABASE_NAME, credentials from a Kubernetes secret),
  kept as a commented block until the SCOPE.md §6 placement decision lands.

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
            ],
        },
    },
]

WSGI_APPLICATION = "datadesk.wsgi.application"

# --- databases --------------------------------------------------------------
#
# Development default: local sqlite. Production is Datadesk's own Postgres
# (application state only — SCOPE.md §1); its placement (shared Cloud SQL
# instance vs dedicated) is a SCOPE.md §6 open decision, so only an
# env-driven seam is provided here.
#
# When the placement decision lands, this block replaces the sqlite default.
# The env contract mirrors MizzouNewsCrawler (k8s/crawler-cronjob.yaml:
# credentials from a cloudsql-db-credentials-style Kubernetes secret) so the
# two systems configure identically:
#
# if env_bool("USE_CLOUD_SQL_CONNECTOR"):
#     DATABASES = {
#         "default": {
#             "ENGINE": "django.db.backends.postgresql",
#             # "project:region:instance" via the Cloud SQL auth proxy or
#             # connector sidecar-free path. Note --auto-iam-authn alone
#             # fails on the existing instance; password auth via Secret
#             # Manager is the proven pattern.
#             # CLOUD_SQL_INSTANCE = os.environ["CLOUD_SQL_INSTANCE"]
#             "NAME": os.environ["DATABASE_NAME"],
#             "USER": os.environ["DATABASE_USER"],
#             "PASSWORD": os.environ["DATABASE_PASSWORD"],
#             "HOST": os.environ.get("DATABASE_HOST", "127.0.0.1"),
#             "PORT": os.environ.get("DATABASE_PORT", "5432"),
#         }
#     }
#
# Read-only crawler-DB and BigQuery connections (Phase 0 infrastructure
# work) are configured separately when those decisions land; they are not
# Django DATABASES entries.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DATADESK_SQLITE_PATH", BASE_DIR / "db.sqlite3"),
    }
}

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
