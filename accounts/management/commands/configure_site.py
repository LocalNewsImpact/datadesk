"""Own this console's row in django_site.

    python manage.py configure_site --domain datadesk.localnewsimpact.org

Datadesk does not read the Site itself — allauth is configured from
settings and there is no email — but `django.contrib.sites` is installed
and the row exists, and since the Source Directory joined this database
the two consoles share the table.

They must not share the *row*. Both applications shipped `SITE_ID = 1`,
so the directory's own configure_site rewrote Datadesk's site to say
"News Source Directory" on sources.localnewsimpact.org. Nothing broke,
because nothing here reads it — which is exactly why it went unnoticed,
and exactly why the next feature that does read it would have been
quietly wrong.

So each console owns a numbered row and says so from its own deploy:
Datadesk is site 1, the directory is site 2. Idempotent, and run on
every release so a hand-edit or another service cannot leave it wrong.
"""

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Point this console's Site row at its own hostname."

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            default="",
            help="defaults to the first real entry in ALLOWED_HOSTS",
        )
        parser.add_argument("--name", default="Datadesk")

    def handle(self, *args, **options):
        domain = options["domain"].strip() or self._infer()
        if not domain or domain in {"*", "example.com"}:
            raise CommandError(
                "no usable domain — pass --domain, or set DJANGO_ALLOWED_HOSTS."
            )

        site, created = Site.objects.get_or_create(pk=settings.SITE_ID)
        was = site.domain
        site.domain = domain
        site.name = options["name"]
        site.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"site {settings.SITE_ID}: {domain}"))
        elif was == domain:
            self.stdout.write(f"site {settings.SITE_ID} already {domain}")
        else:
            self.stdout.write(
                self.style.SUCCESS(f"site {settings.SITE_ID}: {was} -> {domain}")
            )

    @staticmethod
    def _infer():
        for host in settings.ALLOWED_HOSTS:
            # Wildcards and the Cloud Run suffix are not the hostname a
            # person types.
            if host and not host.startswith((".", "*")):
                return host
        return ""
