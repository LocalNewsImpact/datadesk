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
from django.db.models import Value
from django.db.models.functions import Coalesce, Length


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
    #: Written by two stages: the URL rule before extraction, and the
    #: content rule after it. `extraction_telemetry_v2` is what tells the
    #: two apart -- neither writer records which one it was.
    status = models.TextField(null=True)
    #: Why a link is held. "Auto-paused: multiple HTTP 403 responses" on
    #: 19,683 of them, and nothing at all on 7,360 -- which is the
    #: difference between a site that refuses and a link nobody can
    #: account for.
    error_message = models.TextField(null=True)
    #: When discovery first saw the URL. The discovery queue's cohort is
    #: built on this rather than an article's publish date, because a row
    #: the pipeline rejected before extraction has no article and so no
    #: publish date to group by.
    discovered_at = models.DateTimeField(null=True)
    discovered_by = models.TextField(null=True)
    meta = DecodedJSONField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "candidate_links"


class UrlVerification(CrawlerModel):
    """One row per verification decision, written by the crawler.

    The subject of the discovery queue. There is no body here and no
    byline: a URL, the verdict storysniffer returned, and the margin the
    model scored it at.

    `verification_confidence` is a **log-odds margin**, not a
    probability. storysniffer's `predict_proba` saturates -- 97.5% of
    URLs score exactly 0.0 or 1.0 -- so the margin from
    `predict_log_proba` is what carries the ordering, and its magnitudes
    run to thousands. It orders URLs; its scale means nothing.

    The verdict and the margin can disagree, and that disagreement is the
    queue's main signal: storysniffer applies whitelist and blacklist
    overrides after the model predicts, so 12,464 of March's 13,764
    rejections carry a *positive* margin. Those were not the model's call.
    """

    id = models.TextField(primary_key=True)
    candidate_link = models.ForeignKey(
        CandidateLink,
        models.DO_NOTHING,
        db_column="candidate_link_id",
        db_constraint=False,
        null=True,
        related_name="verifications",
    )
    url = models.TextField()
    #: The bare boolean storysniffer.guess() returns, overrides applied.
    storysniffer_result = models.BooleanField(null=True)
    #: The log-odds margin. See the class docstring before ranking on it.
    verification_confidence = models.FloatField(null=True)
    verified_at = models.DateTimeField(null=True)
    previous_status = models.TextField(null=True)
    #: The verdict the pipeline recorded, which is what the reviewer is
    #: being asked to second-guess.
    new_status = models.TextField(null=True)
    article_headline = models.TextField(null=True)
    article_excerpt = models.TextField(null=True)
    dataset_id = models.TextField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "url_verifications"


class Article(CrawlerModel):
    id = models.TextField(primary_key=True)
    candidate_link = models.ForeignKey(
        CandidateLink,
        models.DO_NOTHING,
        db_column="candidate_link_id",
        db_constraint=False,
        related_name="articles",
    )
    # The link's dataset, recorded on the article by the crawler at insert
    # (MizzouNewsCrawler#540) so a dataset's articles are one index range.
    dataset_id = models.TextField(null=True)
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
    #: Characters of captured text, whichever column holds it. Generated
    #: on the crawler's side (crawler #539), so it is never stale and never
    #: written from here -- and `GeneratedField` is what tells the ORM not
    #: to try: a plain IntegerField goes into every INSERT as NULL, which
    #: Postgres refuses on a generated column. Reading it is an integer
    #: fetch; computing it was reading the body out of TOAST, which is
    #: where 16.7 of the queue's 254 seconds went.
    text_length = models.GeneratedField(
        expression=Length(
            Coalesce(
                "content",
                "text",
                "text_excerpt",
                Value(""),
                output_field=models.TextField(),
            )
        ),
        output_field=models.IntegerField(),
        db_persist=True,
    )
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


class ArticlePlaceManual(CrawlerModel):
    """Geography a person put in, for a story the pipeline could not read.

    It lives in its own table rather than in `article_geoids` because the
    crawler DELETEs and rewrites an article's geoid set on every
    enrichment run -- a human row kept there is destroyed by the next
    one, including a run that produced worse geography than the person
    did. `build_story_geoids` reads this and rebuilds `source = 'human'`
    rows from it, so the contribution outlasts the runs.

    See MizzouNewsCrawler/docs/MANUAL_GEOGRAPHY.md.
    """

    id = models.AutoField(primary_key=True)
    article = models.ForeignKey(
        Article,
        models.DO_NOTHING,
        db_column="article_id",
        db_constraint=False,
        related_name="manual_places",
    )
    #: What was typed, kept beside the code it resolved to, so a wrong
    #: resolution can be told from a wrong entry.
    full_name = models.TextField(null=True)
    city = models.TextField(null=True)
    county = models.TextField(null=True)
    state = models.TextField(null=True)
    #: Resolved through `lnic_contracts.geography`, never typed. A
    #: reviewer does not enter a FIPS, and a human entry cannot land on a
    #: rung the pipeline could not have reached.
    geoid = models.TextField(null=True)
    geoid_level = models.TextField(null=True)
    #: The central location, or one of the places the story names.
    is_point = models.BooleanField(default=False)
    added_by = models.TextField()
    added_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "article_places_manual"


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


class Job(CrawlerModel):
    """One run of one pipeline stage.

    The counters were null on all 769 rows until 2026-09-07: the tracker
    held them and neither `complete_operation` nor `fail_operation` passed
    them to the writer, so a job row said a run happened and nothing about
    what it did. Rows written before that date have the columns and no
    values, which is why the processing view shows a dash rather than a
    zero -- a job that processed nothing and a job that never said are
    different facts.
    """

    id = models.TextField(primary_key=True)
    job_type = models.TextField()
    job_name = models.TextField(null=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    exit_status = models.TextField(null=True)
    #: Added 2026-09-07. Null on every row before it, and on any run not
    #: scoped to one dataset.
    dataset_id = models.TextField(null=True)
    records_processed = models.IntegerField(null=True)
    records_created = models.IntegerField(null=True)
    records_updated = models.IntegerField(null=True)
    errors_count = models.IntegerField(null=True)

    @property
    def is_running(self):
        return self.started_at is not None and self.finished_at is None

    class Meta(CrawlerModel.Meta):
        db_table = "jobs"


class ExtractionTelemetry(CrawlerModel):
    """One extraction attempt, whatever came of it.

    `article_id` is a UUID minted before the fetch, so it names a row that
    may never exist -- 156,712 of 315,631 rows on 2026-09-07 matched
    neither `articles` nor `candidate_links`. Those are not failures:
    paused sources and correct topic filtering account for most of them.
    Join on `candidate_link_id`, which is the discovery and exists either
    way, and was added the same day.
    """

    id = models.AutoField(primary_key=True)
    operation_id = models.TextField(null=True)
    url = models.TextField(null=True)
    publisher = models.TextField(null=True)
    host = models.TextField(null=True)
    start_time = models.DateTimeField(null=True)
    end_time = models.DateTimeField(null=True)
    total_duration_ms = models.IntegerField(null=True)
    http_status_code = models.IntegerField(null=True)
    content_length = models.IntegerField(null=True)
    is_success = models.BooleanField(null=True)
    error_message = models.TextField(null=True)
    error_type = models.TextField(null=True)
    created_at = models.DateTimeField(null=True)
    #: Both added 2026-09-07 and null on every row before it. The backfill
    #: (`backfill-telemetry-dataset` in the crawler) fills what it can
    #: prove and leaves the rest null.
    dataset_id = models.TextField(null=True)
    candidate_link_id = models.TextField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "extraction_telemetry_v2"


class BlockedInventory(models.Model):
    """The Blocked page's counts, computed out of band.

    Datadesk's own table, not the crawler's -- the router only diverts
    models carrying `crawler_db`, so this is written to `default`.

    THE PAGE CANNOT COMPUTE THESE
    -----------------------------
    The twenty-one questions behind the Blocked report cost about 97
    seconds against production: several are sequential scans of a 1.5 GB
    `articles` table and an 838 MB telemetry table, and indexing the
    columns that could be indexed took the worst of them from 375s to
    5.8s without changing that. Ninety-seven seconds is a batch job, not
    a page, and Cloud Run cuts a request at 300.

    So the page reads the newest row here and renders immediately, and
    `daily_housekeeping` does the counting -- that job rather than one of
    its own, which is the decision that command already documents: "a
    second Cloud Run job and a second Cloud Scheduler entry per task is
    how a task comes to have neither."

    A day-old snapshot is the right trade for an operations page. Nothing
    on it is actionable within the minute, the pipeline it reports on
    moves in bursts when the crons run, and a number that is a day stale
    is worth incomparably more than a number that never arrives. The page
    shows the age, so staleness is visible rather than assumed.

    History is kept rather than upserted onto one row, because whether
    "never fetched" is growing is a different and more useful question
    than what it is now. At one row a day, 30 days is 30 rows.
    """

    computed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    #: The inventory as `blocked.inventory()` returns it: a list of
    #: {group, label, why, count, publishers}.
    rows = models.JSONField(default=list)
    #: What the counting cost, so a page that has gone stale can say
    #: whether the job is slow or simply not running.
    took_ms = models.IntegerField(default=0)

    class Meta:
        ordering = ["-computed_at"]
        verbose_name_plural = "blocked inventories"

    def __str__(self):
        return f"blocked inventory at {self.computed_at:%Y-%m-%d %H:%M}"
