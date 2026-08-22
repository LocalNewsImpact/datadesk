"""Account administration URLs. Admin role only, enforced per view."""

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("users/", views.users, name="users"),
    path("roles/", views.roles, name="roles"),
    path("roles/set/", views.set_role, name="set_role"),
]
