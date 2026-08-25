"""Account administration URLs. Admin role only, enforced per view."""

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("users/", views.users, name="users"),
    path("accounts/new/", views.add_account, name="add_account"),
    path("invite/", views.invite, name="invite"),
    path("uninvite/", views.uninvite, name="uninvite"),
    # Not "datasets/set/": an existing `datasets/<slug>/` route reads
    # "set" as a slug and wins, so that address resolved to a dataset
    # called "set" rather than to this.
    path("grants/set/", views.set_dataset_grant, name="set_dataset_grant"),
    path("roles/", views.roles, name="roles"),
    path("roles/set/", views.set_role, name="set_role"),
]
