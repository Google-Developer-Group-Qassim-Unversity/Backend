"""Staging and local dev never email the people in their copy of prod.

See app/services/email_redirect.py. These assert on the request that would
have left the process (the ``outbound`` fixture), so they cover what
send-certificates would actually have been asked to send.
"""

import asyncio

import pytest

from app.config import reload_settings
from app.DB.schema import EmailLogsFromAddress, EmailProvider, MembersGender
from app.exceptions import ServiceUnavailable
from app.routers.email_models import CertificateLanguage, CertificateRequest, SimpleEvent, SimpleMember
from app.services.email_gateway import call_blast_api, call_certificate_api, call_direct_email_api


@pytest.fixture
def env(monkeypatch):
    def set_env(name: str, fallback: str = ""):
        monkeypatch.setenv("ENV", name)
        monkeypatch.setenv("EMAIL_REDIRECT_FALLBACK", fallback)
        reload_settings()

    return set_env


def blast(emails: list[str], on_behalf_of: int | None, html: str = "<p>hello</p>"):
    return asyncio.run(
        call_blast_api(
            emails,
            "subject",
            html,
            EmailProvider.GOOGLE,
            EmailLogsFromAddress.GDG_QASSIM,
            None,
            [],
            on_behalf_of=on_behalf_of,
        )
    )


def test_production_sends_as_addressed(env, outbound, seed_refs):
    env("Production")
    blast([seed_refs.sara.email], on_behalf_of=seed_refs.ahmed.id)

    assert outbound.one("/blasts").params.get_list("emails") == [seed_refs.sara.email]


def test_staging_sends_to_the_member_who_clicked(env, outbound, seed_refs):
    env("Staging")
    blast([seed_refs.sara.email, "someone@example.com"], on_behalf_of=seed_refs.ahmed.id)

    sent = outbound.one("/blasts")
    assert sent.params.get_list("emails") == [seed_refs.ahmed.email]
    # the copy says who it was really for, ahead of the original body
    assert "Staging copy." in sent.text
    assert seed_refs.sara.email in sent.text
    assert "someone@example.com" in sent.text
    assert sent.text.endswith("<p>hello</p>")


def test_local_development_redirects_too(env, outbound, seed_refs):
    env("development")
    blast([seed_refs.sara.email], on_behalf_of=seed_refs.ahmed.id)

    assert outbound.one("/blasts").params.get_list("emails") == [seed_refs.ahmed.email]


def test_staging_without_a_click_goes_to_the_fallback_list(env, outbound, seed_refs):
    env("Staging", fallback="dev1@example.com, dev2@example.com")
    blast([seed_refs.sara.email], on_behalf_of=None)

    assert outbound.one("/blasts").params.get_list("emails") == ["dev1@example.com", "dev2@example.com"]


def test_staging_with_nowhere_to_redirect_sends_nothing(env, outbound, seed_refs):
    env("Staging")
    with pytest.raises(ServiceUnavailable):
        blast([seed_refs.sara.email], on_behalf_of=None)

    assert outbound.to("/blasts") == []


def test_a_long_recipient_list_is_summarised(env, outbound, seed_refs):
    env("Staging")
    blast([f"member{i}@example.com" for i in range(60)], on_behalf_of=seed_refs.ahmed.id)

    sent = outbound.one("/blasts").text
    assert "member49@example.com" in sent
    assert "member50@example.com" not in sent
    assert "and 10 more" in sent


def test_a_certificate_is_readdressed_on_staging(env, outbound, seed_refs):
    env("Staging")
    sara = seed_refs.sara
    call_certificate_api(
        CertificateRequest(
            event=SimpleEvent(name="event", date="1 July 2026", official=False),
            member=SimpleMember(name=sara.name, email=sara.email, gender=MembersGender.FEMALE),
            language=CertificateLanguage.ARABIC,
        ),
        on_behalf_of=seed_refs.ahmed.id,
    )

    member = outbound.one("/emails/certificate").json["member"]
    assert member["email"] == seed_refs.ahmed.email
    assert member["name"] == sara.name  # still Sara's certificate


def test_a_single_recipient_email_goes_once_to_each_fallback_address(env, outbound, seed_refs):
    env("Staging", fallback="dev1@example.com,dev2@example.com")
    asyncio.run(
        call_direct_email_api(
            seed_refs.sara.email, "subject", "<p>hi</p>", [], EmailProvider.GOOGLE, None, on_behalf_of=None
        )
    )

    sent = outbound.to("/emails/direct")
    assert [request.json["recipient_email"] for request in sent] == ["dev1@example.com", "dev2@example.com"]
    assert all(seed_refs.sara.email in request.json["html_content"] for request in sent)
