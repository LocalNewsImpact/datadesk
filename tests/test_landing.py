"""Landing page: sign-in for visitors, email and role for users."""

import pytest

from accounts.models import DATADESK, Grant


@pytest.mark.django_db
def test_anonymous_gets_sign_in(client):
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Datadesk" in content
    assert "Sign in" in content


# A role assigned means the view also asks the crawler alias for row counts.
@pytest.mark.django_db(databases=["default", "crawler"])
def test_authenticated_sees_email_and_how_access_is_granted(client, django_user_model):
    """The page named the person's single role. Roles are per dataset
    now, so there is no single one to name -- it says how access works
    instead."""
    user = django_user_model.objects.create_user("v1", email="v1@example.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="viewer")
    client.force_login(user)
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "v1@example.org" in content
    assert "Granted per dataset" in content


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
def test_sign_in_page_offers_both_doors_and_no_signup(client, settings):
    """Two kinds of account, so two ways in. Signup is still closed.

    The page offered Google alone, which is what it was when Google was
    the only way in -- so an account made with a password could set one
    from the link it was sent and then had nowhere to type it.
    """
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
    # ...and somewhere to type a password an administrator gave out.
    assert 'type="password"' in content, "a password account has nowhere to sign in"
    assert "administrator gave you a password" in content
    # Nobody registers themselves, which is the part that has not changed.
    assert "sign up" not in content.lower()
    assert "created by an administrator" in content
