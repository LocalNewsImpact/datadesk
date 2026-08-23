"""Explorer URLs."""

from django.urls import path

from explorer import views

app_name = "explorer"

urlpatterns = [
    path("articles/", views.articles, name="articles"),
    path("sources/", views.sources, name="sources"),
    path("enrichment/", views.enrichment, name="enrichment"),
    path("costs/", views.costs, name="costs"),
    path("articles/<str:article_id>/", views.article_detail, name="article_detail"),
]
