"""Unmanaged models over the crawler's schema (SCOPE.md §1).

Columns mirror MizzouNewsCrawler src/models/__init__.py and the
article_enrichment table its enrichment repository maintains — only the
columns the explorer reads; the crawler owns the schema and migrations.
`managed = False` keeps Django's migration machinery away, and
explorer.routers.CrawlerRouter routes every query to the read-only
`crawler` alias (datadesk_ro, SELECT-only by Postgres grant).

FKs carry db_constraint=False: the constraints exist (or don't) in the
crawler's schema; Django only needs the join paths.
"""

import json

from django.db import models


class DecodedJSONField(models.JSONField):
    """A JSONField that tolerates values the driver already decoded.

    The crawler's JSON columns are Postgres `json`, not `jsonb`. Django's
    psycopg3 backend registers its raw-string loader for jsonb only, so a
    `json` column arrives as a parsed dict/list and JSONField.from_db_value
    then calls json.loads on it — TypeError: the JSON object must be str,
    bytes or bytearray, not dict. We do not own that schema, so the field
    accommodates both shapes instead.
    """

    def from_db_value(self, value, expression, connection):
        if isinstance(value, (dict, list)):
            return value
        return super().from_db_value(value, expression, connection)


class CrawlerModel(models.Model):
    """Marker base: routed to the crawler alias, never migrated."""

    crawler_db = True

    class Meta:
        abstract = True
        managed = False


class Dataset(CrawlerModel):
    id = models.TextField(primary_key=True)
    slug = models.TextField(unique=True)
    label = models.TextField(unique=True)
    name = models.TextField(null=True)
    description = models.TextField(null=True)
    # datasets.metadata carries default_state and the enrichment profile.
    meta = DecodedJSONField(db_column="metadata", null=True)
    # Who to credit and who to ask, for a dataset whose charts end up
    # embedded in other people's pages. Not the grants in `accounts`:
    # those say who may read this, which is access control, and publishing
    # them as attribution would put staff addresses into a public feed.
    # Null where nobody has said -- a contact that reaches nobody is a
    # worse answer than no contact.
    owner_name = models.TextField(null=True, blank=True)
    owner_email = models.TextField(null=True, blank=True)
    cron_enabled = models.BooleanField(default=True)

    class Meta(CrawlerModel.Meta):
        db_table = "datasets"

    def __str__(self):
        return self.label


class Source(CrawlerModel):
    id = models.TextField(primary_key=True)
    host = models.TextField()
    host_norm = models.TextField(unique=True)
    canonical_name = models.TextField(null=True)
    city = models.TextField(null=True)
    county = models.TextField(null=True)
    owner = models.TextField(null=True)
    type = models.TextField(null=True)
    status = models.TextField(null=True, default="active")
    meta = DecodedJSONField(db_column="metadata", null=True)
    # NOT NULL without server defaults in the crawler's schema; creation
    # must supply them (see create_crawler_write_role.sql INSERT columns).
    rss_consecutive_failures = models.IntegerField(default=0)
    rss_transient_failures = DecodedJSONField(default=list)

    # Paywalls, and getting through them.
    #
    # `requires_login` is the crawler's: the extractor performs a browser
    # login for this publisher, which is true of the seven that are
    # configured. `has_paywall` is the wider fact about the publication,
    # ticked on a record long before anybody automates a login for it.
    #
    # The credentials are not here and are not going to be. They live in
    # Secret Manager under `auth_secret_name`, which is why `auth_config`
    # carries the crawler's comment that credentials are never stored in
    # it: a password column would be readable by every role holding SELECT
    # on this table, including the read-only analytics role and every CSV
    # anybody exports.
    has_paywall = models.BooleanField(default=False)
    requires_login = models.BooleanField(default=False)
    auth_type = models.TextField(null=True, blank=True)
    auth_secret_name = models.TextField(null=True, blank=True)
    auth_config = DecodedJSONField(null=True, blank=True)
    subscription_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    subscription_period = models.TextField(null=True, blank=True)
    login_url = models.TextField(null=True, blank=True)

    class Meta(CrawlerModel.Meta):
        db_table = "sources"

    def __str__(self):
        return self.canonical_name or self.host


class DatasetSource(CrawlerModel):
    id = models.TextField(primary_key=True)
    dataset = models.ForeignKey(
        Dataset,
        models.DO_NOTHING,
        db_column="dataset_id",
        db_constraint=False,
        related_name="memberships",
    )
    source = models.ForeignKey(
        Source,
        models.DO_NOTHING,
        db_column="source_id",
        db_constraint=False,
        related_name="memberships",
    )

    class Meta(CrawlerModel.Meta):
        db_table = "dataset_sources"


class Gazetteer(CrawlerModel):
    id = models.TextField(primary_key=True)
    dataset_id = models.TextField(null=True)
    source_id = models.TextField(null=True)
    category = models.TextField(null=True)
    created_at = models.DateTimeField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "gazetteer"


class CandidateLink(CrawlerModel):
    id = models.TextField(primary_key=True)
    url = models.TextField()
    # The crawler's `source` column is the publisher name string; the
    # normalized relation is source_id → sources.
    source_name_raw = models.TextField(db_column="source", null=True)
    source = models.ForeignKey(
        Source,
        models.DO_NOTHING,
        db_column="source_id",
        db_constraint=False,
        null=True,
        related_name="candidate_links",
    )
    dataset_id = models.TextField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "candidate_links"


class Article(CrawlerModel):
    id = models.TextField(primary_key=True)
    candidate_link = models.ForeignKey(
        CandidateLink,
        models.DO_NOTHING,
        db_column="candidate_link_id",
        db_constraint=False,
        related_name="articles",
    )
    url = models.TextField(null=True)
    title = models.TextField(null=True)
    author = models.TextField(null=True)
    publish_date = models.DateTimeField(null=True)
    content = models.TextField(null=True)
    # The crawler keeps an older `text` column for compatibility; content
    # is the current field and the one review edits will target.
    text = models.TextField(null=True)
    text_excerpt = models.TextField(null=True)
    #: How many times enrichment has tried. The selector requires it to be
    #: under the limit (3), so a rejection that rewinds the status is only
    #: a decision if this is under it too -- otherwise the article sits at
    #: `labeled` and is never picked up.
    enrichment_attempts = models.IntegerField(null=True)
    #: The crawler's own notes on the row. The review hold writes what it
    #: held here, because `status` is overwritten by `in_review` and the
    #: claim being reviewed has nowhere else to live. housekeeping already
    #: uses it the same way, for pause_reason.
    #:
    #: DecodedJSONField, like every other JSON column here: this is
    #: Postgres `json`, which psycopg3 hands back already parsed. A plain
    #: JSONField calls json.loads on the dict and raises, which was a 500
    #: on /review/queue/ for any held article. sqlite returned text and
    #: the suite never saw it.
    metadata = DecodedJSONField(null=True)
    #: Where the page as captured is archived. The bucket has 30-day
    #: retention, so a row older than that has none -- which is what
    #: decides whether a body can be re-parsed or is simply gone.
    raw_gcs_path = models.TextField(null=True)
    status = models.TextField()
    wire_check_status = models.TextField()
    # The wire check's own findings: a JSON array naming the syndication
    # services detected. Empty or absent on a local story.
    wire = DecodedJSONField(null=True)
    created_at = models.DateTimeField()
    primary_label = models.TextField(null=True)
    primary_label_confidence = models.FloatField(null=True)
    alternate_label = models.TextField(null=True)
    alternate_label_confidence = models.FloatField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "articles"

    @property
    def is_wire(self):
        """True only when the check found syndication.

        wire_check_status carries two passing values — 'complete' and
        'local', the latter a legacy pass — so "not 'complete'" is not a
        test for wire. Only 'wire', or findings in the wire column, are.
        """
        return self.wire_check_status == "wire" or bool(self.wire_services())

    @property
    def wire_check_concluded(self):
        """False while the check errored or never ran."""
        return self.wire_check_status in ("complete", "local", "wire")

    def wire_services(self):
        """Names of the syndication services the check detected.

        The column's shape is the crawler's, and is not in the schema
        dump beyond `json`; this reads a list of names, a list of objects
        carrying a name, or a bare string, and returns [] for anything
        else rather than raising in a template.
        """
        raw = self.wire
        if not raw:
            return []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        names = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                for key in ("service", "name", "source", "agency", "wire"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        names.append(value.strip())
                        break
        return names


class ContentTypeDetection(CrawlerModel):
    """What the content type detector recorded about its own verdict.

    Its confidence, the reason and the evidence are written here rather
    than to articles.metadata, which is why an earlier reading concluded
    the reasoning had been discarded. Coverage since 2025-11-07: weather
    100%, obituary 97.7%, opinion 95.6%, wire 12.8% -- wire is set by
    several paths and only this one writes here.
    """

    #: `integer` with a sequence in the crawler, not text. The suite ran
    #: on sqlite, which stores whatever it is given, so a TextField over
    #: an integer column went unnoticed here.
    id = models.AutoField(primary_key=True)
    #: A log rather than a record: an article can have more than one row,
    #: so a query that joins it must not multiply the article.
    article = models.ForeignKey(
        "explorer.Article",
        on_delete=models.DO_NOTHING,
        db_column="article_id",
        related_name="detections",
        null=True,
        db_constraint=False,
    )
    detected_type = models.TextField(null=True)
    detection_method = models.TextField(null=True)
    confidence_score = models.FloatField(null=True)
    reason = models.TextField(null=True)
    #: TEXT in Postgres, not jsonb. Declaring it JSONField let the ORM
    #: emit `?|` for a key lookup, which Postgres refuses on text -- and
    #: the error surfaced as "crawler database not connected", because the
    #: view catches DatabaseError and cannot tell a broken query from a
    #: broken connection. SQLite accepted it, so the tests did too.
    evidence = models.TextField(null=True)

    def evidence_keys(self):
        """The keys the detector recorded, or () if it recorded nothing."""
        import json

        try:
            loaded = json.loads(self.evidence or "")
        except (TypeError, ValueError):
            return ()
        return tuple(loaded) if isinstance(loaded, dict) else ()

    class Meta(CrawlerModel.Meta):
        db_table = "content_type_detection_telemetry"


class ArticleEnrichment(CrawlerModel):
    # One row per article (the crawler upserts ON CONFLICT (article_id)).
    article = models.OneToOneField(
        Article,
        models.DO_NOTHING,
        db_column="article_id",
        db_constraint=False,
        primary_key=True,
        related_name="enrichment",
    )
    #: `integer` in the crawler. Declared TextField, a comparison against
    #: a version number would have been str against int and quietly
    #: false. Found by check_crawler_schema, not by a test.
    profile_version = models.IntegerField(null=True)
    skip_reason = models.TextField(null=True)
    model = models.TextField(null=True)
    #: `numeric(10, 6)`, so psycopg hands back a Decimal whatever this
    #: says. Declared FloatField it was a lie that happened not to break:
    #: the rollups sum with a leading int, and Decimal + int is fine.
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    enriched_at = models.DateTimeField(null=True)
    is_news_content = models.BooleanField(null=True)
    content_gate_reason = models.TextField(null=True)
    scope = models.TextField(null=True)
    scope_confidence = models.FloatField(null=True)
    subject = models.TextField(null=True)
    subject_confidence = models.FloatField(null=True)
    topic = models.TextField(null=True)
    topic_confidence = models.FloatField(null=True)
    format = models.TextField(null=True)
    format_confidence = models.FloatField(null=True)
    timeframe = models.TextField(null=True)
    timeframe_confidence = models.FloatField(null=True)
    user_need = models.TextField(null=True)
    user_need_confidence = models.FloatField(null=True)
    rationales = DecodedJSONField(null=True)
    point_place = models.TextField(null=True)
    point_method = models.TextField(null=True)
    point_geoid = models.TextField(null=True)
    point_geoid_level = models.TextField(null=True)
    point_lat = models.FloatField(null=True)
    point_lon = models.FloatField(null=True)
    point_zcta = models.TextField(null=True)
    # `geoids` is a text column holding a JSON array of MENTIONED FIPS
    # codes. The central claim (point_geoid) is never repeated here; the
    # two are separate assertions. mentioned_geoids() parses it.
    geoids = models.TextField(null=True)
    geo_skip_reason = models.TextField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "article_enrichment"

    @property
    def has_point(self):
        """True when the record carries a central-geography claim."""
        return bool(self.point_geoid)

    def mentioned_geoids(self):
        """The mention list, parsed from the `geoids` text column.

        Stored as a JSON array in the March backfill. Tolerates a
        comma-separated string, which older rows may carry, and returns []
        for anything it cannot read rather than raising in a template.
        """
        raw = self.geoids
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(item) for item in raw if item]
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return [part.strip() for part in str(raw).split(",") if part.strip()]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
        if isinstance(parsed, str):
            return [parsed] if parsed else []
        return []


class ArticleGeoid(CrawlerModel):
    """The superset of geographies for an article: the central claim
    (is_primary) plus every mention, each with the rung it resolved to."""

    id = models.BigAutoField(primary_key=True)
    article = models.ForeignKey(
        Article,
        models.DO_NOTHING,
        db_column="article_id",
        db_constraint=False,
        related_name="geoid_rows",
    )
    geoid = models.TextField()
    geoid_level = models.TextField()
    is_primary = models.BooleanField(default=False)
    source = models.TextField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "article_geoids"


class ArticlePlace(CrawlerModel):
    id = models.BigAutoField(primary_key=True)
    article = models.ForeignKey(
        Article,
        models.DO_NOTHING,
        db_column="article_id",
        db_constraint=False,
        related_name="places",
    )
    full_name = models.TextField(null=True)
    place_type = models.TextField(null=True)
    city = models.TextField(null=True)
    county = models.TextField(null=True)
    state = models.TextField(null=True)
    address = models.TextField(null=True)
    description = models.TextField(null=True)
    mention_text = models.TextField(null=True)
    is_point = models.BooleanField(null=True)
    lat = models.FloatField(null=True)
    lon = models.FloatField(null=True)
    geocoder = models.TextField(null=True)
    geoid = models.TextField(null=True)
    geoid_level = models.TextField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "article_places"

    def __str__(self):
        return self.full_name or self.mention_text or ""


class ArticlePerson(CrawlerModel):
    id = models.BigAutoField(primary_key=True)
    article = models.ForeignKey(
        Article,
        models.DO_NOTHING,
        db_column="article_id",
        db_constraint=False,
        related_name="people",
    )
    name = models.TextField()
    sort_key = models.TextField(null=True)
    title = models.TextField(null=True)
    affiliation = models.TextField(null=True)
    person_type = models.TextField(null=True)
    role_in_story = models.TextField(null=True)
    nature = models.TextField(null=True)
    public_figure = models.BooleanField(null=True)
    mention_count = models.IntegerField(null=True)
    quotes = DecodedJSONField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "article_people"

    def __str__(self):
        return self.name


class ArticleOrganization(CrawlerModel):
    id = models.BigAutoField(primary_key=True)
    article = models.ForeignKey(
        Article,
        models.DO_NOTHING,
        db_column="article_id",
        db_constraint=False,
        related_name="organizations",
    )
    name = models.TextField()
    org_type = models.TextField(null=True)
    boundary = models.TextField(null=True)
    role_in_story = models.TextField(null=True)
    nature = models.TextField(null=True)
    mention_count = models.IntegerField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "article_organizations"

    def __str__(self):
        return self.name
