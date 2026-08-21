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


@pytest.mark.django_db
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
