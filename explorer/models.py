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

    class Meta(CrawlerModel.Meta):
        db_table = "sources"

    def __str__(self):
        return self.canonical_name or self.host


class DatasetSource(CrawlerModel):
    id = models.TextField(primary_key=True)
    dataset = models.ForeignKey(
        Dataset, models.DO_NOTHING, db_column="dataset_id", db_constraint=False
    )
    source = models.ForeignKey(
        Source, models.DO_NOTHING, db_column="source_id", db_constraint=False
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
    status = models.TextField()
    wire_check_status = models.TextField()
    created_at = models.DateTimeField()
    primary_label = models.TextField(null=True)
    primary_label_confidence = models.FloatField(null=True)
    alternate_label = models.TextField(null=True)
    alternate_label_confidence = models.FloatField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "articles"


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
    profile_version = models.TextField(null=True)
    skip_reason = models.TextField(null=True)
    model = models.TextField(null=True)
    cost_usd = models.FloatField(null=True)
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
    geoids = models.TextField(null=True)
    geo_skip_reason = models.TextField(null=True)

    class Meta(CrawlerModel.Meta):
        db_table = "article_enrichment"
