"""Explorer URLs."""

from django.urls import path

from explorer import views

app_name = "explorer"

urlpatterns = [
    path("articles/", views.articles, name="articles"),
    path("articles/<str:article_id>/", views.article_detail, name="article_detail"),
]
