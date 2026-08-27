"""Account administration URLs. Admin role only, enforced per view."""

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("users/", views.users, name="users"),
    # One account, and everything an admin can do to it. Spread across
    # three screens, an admin correcting a mistake had to know which
    # screen each half lived on.
    path("users/<int:user_id>/", views.person, name="person"),
    path("users/<int:user_id>/email/", views.set_email, name="set_email"),
    path("users/<int:user_id>/name/", views.set_name, name="set_name"),
    path(
        "users/<int:user_id>/password-link/",
        views.send_password_link,
        name="send_password_link",
    ),
    path("users/<int:user_id>/active/", views.set_active, name="set_active"),
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
