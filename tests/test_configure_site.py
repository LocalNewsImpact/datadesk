"""Each console owns its own row in the shared django_site.

The Source Directory now runs against this database. `django_site` is
one of the tables they share, and both applications shipped
`SITE_ID = 1`, so the directory's deploy rewrote Datadesk's site to say
"News Source Directory" on sources.localnewsimpact.org.

Nothing broke — Datadesk configures allauth from settings, sends no
email, and never reads the Site. That is precisely why it went
unnoticed, and precisely why the next feature to read it would have been
quietly wrong. Each console now owns a numbered row and asserts it on
every deploy.
"""

import pytest
from django.conf import settings as django_settings
from django.contrib.sites.models import Site
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.django_db


def test_it_claims_this_consoles_row():
    call_command("configure_site", domain="datadesk.localnewsimpact.org")
    site = Site.objects.get(pk=django_settings.SITE_ID)
    assert site.domain == "datadesk.localnewsimpact.org"
    assert site.name == "Datadesk"


def test_it_takes_the_row_back_if_something_else_took_it():
    """The failure this exists for: the directory's deploy pointed site 1
    at its own hostname. Running from Datadesk's deploy takes it back."""
    Site.objects.update_or_create(
        pk=django_settings.SITE_ID,
        defaults={
            "domain": "sources.localnewsimpact.org",
            "name": "News Source Directory",
        },
    )
    call_command("configure_site", domain="datadesk.localnewsimpact.org")
    site = Site.objects.get(pk=django_settings.SITE_ID)
    assert site.domain == "datadesk.localnewsimpact.org"
    assert site.name == "Datadesk"


def test_running_it_twice_changes_nothing():
    call_command("configure_site", domain="datadesk.localnewsimpact.org")
    before = Site.objects.get(pk=django_settings.SITE_ID).domain
    call_command("configure_site", domain="datadesk.localnewsimpact.org")
    assert Site.objects.get(pk=django_settings.SITE_ID).domain == before
    assert Site.objects.filter(pk=django_settings.SITE_ID).count() == 1


def test_it_falls_back_to_the_allowed_host(settings):
    """The deploy passes --domain, but a hand-run without one should
    still land on the right hostname rather than example.com."""
    settings.ALLOWED_HOSTS = ["datadesk.localnewsimpact.org", ".run.app"]
    call_command("configure_site")
    assert (
        Site.objects.get(pk=django_settings.SITE_ID).domain
        == "datadesk.localnewsimpact.org"
    )


def test_it_refuses_a_domain_that_would_break_oauth(settings):
    """example.com is Django's default and a wildcard is not a hostname.
    Writing either sends Google a callback that cannot work."""
    settings.ALLOWED_HOSTS = ["*"]
    with pytest.raises(CommandError):
        call_command("configure_site")
    with pytest.raises(CommandError):
        call_command("configure_site", domain="example.com")
