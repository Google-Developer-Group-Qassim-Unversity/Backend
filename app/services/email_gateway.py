"""Outbound calls to the certificate and blast APIs.

Every function here maps transport failures onto the exception types in
app/exceptions.py, so routers never see an httpx error.
"""

import json
import logging

import httpx

from app.clients import get_http_client
from app.config import config
from app.DB.schema import EmailLogsFromAddress, EmailProvider
from app.exceptions import BadGateway, GatewayTimeout, ServiceUnavailable
from app.routers.email_models import (
    BlastAttachment,
    BlaseResponse,
    CertificateLanguage,
    CertificateRequest,
    CustomEmailAttachment,
    SimpleEvent,
    SimpleMember,
)

logger = logging.getLogger(__name__)


async def call_acceptance_api(
    emails: list[str], subject: str, html_content: str, from_address: EmailLogsFromAddress
) -> BlaseResponse:
    client = get_http_client()
    try:
        response = await client.post(
            f"{config.CERTIFICATE_API_URL}/blasts",
            params={"emails": emails, "subject": subject, "provider": "google", "from_address": from_address.value},
            content=html_content,
            headers={"Content-Type": "text/html; charset=utf-8"},
            timeout=60.0,
        )
        response.raise_for_status()
        response_data = BlaseResponse.model_validate(response.json())
        return response_data
    except httpx.TimeoutException:
        raise GatewayTimeout(detail="Acceptance API request timed out")
    except httpx.HTTPStatusError as e:
        raise BadGateway(detail=f"Acceptance API returned error: {e.response.status_code}")
    except httpx.RequestError:
        raise ServiceUnavailable(detail="Failed to connect to acceptance API")


async def call_blast_api(
    emails: list[str],
    subject: str,
    html_content: str,
    provider: EmailProvider,
    from_address: EmailLogsFromAddress | None,
    preview_text: str | None,
    attachments: list[BlastAttachment],
) -> BlaseResponse:
    # httpx serializes a None param value as an empty string rather than omitting the key,
    # which send-certificates' `EmailLogsFromAddress | None` Query rejects as invalid (422) --
    # so from_address/preview_text are only included when actually set.
    params: dict[str, object] = {
        "emails": emails,
        "subject": subject,
        "provider": provider.value,
        "attachments": json.dumps([a.model_dump(mode="json") for a in attachments]),
    }
    if from_address is not None:
        params["from_address"] = from_address.value
    if preview_text is not None:
        params["preview_text"] = preview_text

    # Gmail SMTP sends a blast as a single BCC message but still issues one RCPT TO per
    # recipient over the same connection (~0.2s each observed in prod), so large batches
    # take proportionally longer than a flat timeout can account for.
    timeout = max(60.0, len(emails) * 0.5 + 60.0)
    client = get_http_client()
    try:
        response = await client.post(
            f"{config.CERTIFICATE_API_URL}/blasts",
            params=params,
            content=html_content,
            headers={"Content-Type": "text/html; charset=utf-8"},
            timeout=timeout,
        )
        response.raise_for_status()
        response_data = BlaseResponse.model_validate(response.json())
        return response_data
    except httpx.TimeoutException:
        raise GatewayTimeout(detail="Blast API request timed out")
    except httpx.HTTPStatusError as e:
        raise BadGateway(detail=f"Blast API returned error: {e.response.status_code}")
    except httpx.RequestError:
        raise ServiceUnavailable(detail="Failed to connect to blast API")


def call_certificate_api(cert_request: CertificateRequest) -> dict:
    with httpx.Client(timeout=120.0) as client:
        try:
            response = client.post(
                f"{config.CERTIFICATE_API_URL}/emails/certificate",
                json=cert_request.model_dump(mode="json"),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise GatewayTimeout(detail="Certificate API request timed out")
        except httpx.HTTPStatusError as e:
            raise BadGateway(detail=f"Certificate API returned error: {e.response.status_code}")
        except httpx.RequestError:
            raise ServiceUnavailable(detail="Failed to connect to certificate API")


async def call_custom_email_api(
    recipient_email: str,
    subject: str,
    html_content: str,
    attachments: list[CustomEmailAttachment],
    event: SimpleEvent,
    member: SimpleMember,
    language: CertificateLanguage,
    provider: EmailProvider,
    from_address: EmailLogsFromAddress | None,
) -> dict:
    client = get_http_client()
    try:
        response = await client.post(
            f"{config.CERTIFICATE_API_URL}/emails/custom",
            json={
                "recipient_email": recipient_email,
                "subject": subject,
                "html_content": html_content,
                "event": event.model_dump(mode="json"),
                "member": member.model_dump(mode="json"),
                "language": language.value,
                "attachments": [a.model_dump(mode="json") for a in attachments],
                "provider": provider.value,
                "from_address": from_address.value if from_address else None,
            },
            headers={"Content-Type": "application/json"},
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException:
        raise GatewayTimeout(detail="Custom email API request timed out")
    except httpx.HTTPStatusError as e:
        raise BadGateway(detail=f"Custom email API returned error: {e.response.status_code}")
    except httpx.RequestError:
        raise ServiceUnavailable(detail="Failed to connect to custom email API")


async def call_direct_email_api(
    recipient_email: str,
    subject: str,
    html_content: str,
    attachments: list[CustomEmailAttachment],
    provider: EmailProvider,
    from_address: EmailLogsFromAddress | None,
) -> dict:
    client = get_http_client()
    try:
        response = await client.post(
            f"{config.CERTIFICATE_API_URL}/emails/direct",
            json={
                "recipient_email": recipient_email,
                "subject": subject,
                "html_content": html_content,
                "attachments": [a.model_dump(mode="json") for a in attachments],
                "provider": provider.value,
                "from_address": from_address.value if from_address else None,
            },
            headers={"Content-Type": "application/json"},
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException:
        raise GatewayTimeout(detail="Direct email API request timed out")
    except httpx.HTTPStatusError as e:
        raise BadGateway(detail=f"Direct email API returned error: {e.response.status_code}")
    except httpx.RequestError:
        raise ServiceUnavailable(detail="Failed to connect to direct email API")
