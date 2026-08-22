"""Landing page: sign-in for visitors, email and role for users."""

import pytest
from django.contrib.auth.models import Group


@pytest.mark.django_db
def test_anonymous_gets_sign_in(client):
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Datadesk" in content
    assert "Sign in" in content


# A role assigned means the view also asks the crawler alias for row counts.
@pytest.mark.django_db(databases=["default", "crawler"])
def test_authenticated_sees_email_and_role(client, django_user_model):
    user = django_user_model.objects.create_user("v1", email="v1@example.org")
    user.groups.add(Group.objects.get(name="viewer"))
    client.force_login(user)
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "v1@example.org" in content
    assert "viewer" in content


@pytest.mark.django_db
def test_authenticated_without_role(client, django_user_model):
    user = django_user_model.objects.create_user("v2", email="v2@example.org")
    client.force_login(user)
    response = client.get("/")
    assert "none assigned" in response.content.decode()


def test_health_endpoint(client, db):
    """/_health returns 200 and touches the database (deploy.yml probes it)."""
    response = client.get("/_health")
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_sign_in_page_offers_google_and_not_signup(client, settings):
    """Signup is closed and Google is the only path, so the page must not
    invite either a password or an account of one's own."""
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "APP": {"client_id": "x", "secret": "y", "key": ""},
            "SCOPE": ["profile", "email"],
        }
    }
    response = client.get("/accounts/login/")
    content = response.content.decode()
    assert response.status_code == 200
    assert "Continue with Google" in content
    assert "sign up" not in content.lower()
    assert "Remember Me" not in content
    assert "created by an administrator" in content
