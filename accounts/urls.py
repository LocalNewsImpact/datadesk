"""Account administration URLs. Admin role only, enforced per view."""

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("users/", views.users, name="users"),
    path("accounts/new/", views.add_account, name="add_account"),
    path("invite/", views.invite, name="invite"),
    path("uninvite/", views.uninvite, name="uninvite"),
    path("roles/", views.roles, name="roles"),
    path("roles/set/", views.set_role, name="set_role"),
]
