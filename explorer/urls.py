"""Explorer URLs."""

from django.urls import path

from explorer import views

app_name = "explorer"

urlpatterns = [
    path("articles/", views.articles, name="articles"),
]
