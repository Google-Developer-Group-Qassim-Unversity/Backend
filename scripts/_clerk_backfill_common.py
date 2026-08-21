"""Shared Clerk Backend API helpers for the clerk_user_id / verified-email
backfill scripts (backfill_clerk_user_id.py, backfill_verified_personal_emails.py).

Both scripts need a full email -> Clerk user id map to work safely:
  - backfill_clerk_user_id uses it to find each member's Clerk user id.
  - backfill_verified_personal_emails uses it to detect collisions (an email
    already claimed by a *different* Clerk user) before writing anything.

Clerk's GET /v1/users email_address[] filter was found unreliable in manual
testing, so instead of trusting server-side filtering we page through every
user once and build the map ourselves.
"""

import os
import time

import httpx

CLERK_API_BASE = "https://api.clerk.com/v1"
PAGE_SIZE = 500
PAGE_DELAY_SECONDS = 0.2


def clerk_secret_key() -> str:
    key = os.environ.get("CLERK_SECRET_KEY")
    if not key:
        raise RuntimeError("CLERK_SECRET_KEY is not set in the environment")
    return key


def fetch_all_clerk_users(secret_key: str) -> list[dict]:
    """Pages through every Clerk user. Returns raw user objects."""
    users: list[dict] = []
    offset = 0
    headers = {"Authorization": f"Bearer {secret_key}"}

    with httpx.Client(base_url=CLERK_API_BASE, headers=headers, timeout=30) as client:
        while True:
            resp = client.get("/users", params={"limit": PAGE_SIZE, "offset": offset})
            resp.raise_for_status()
            page = resp.json()
            if not page:
                break
            users.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            time.sleep(PAGE_DELAY_SECONDS)

    return users


def build_email_to_user_ids(users: list[dict]) -> dict[str, list[str]]:
    """Maps lowercased email -> list of Clerk user ids that have it (any of
    their email_addresses, not just primary). A list with >1 entry means the
    same email is claimed by more than one Clerk user - a collision.
    """
    email_map: dict[str, list[str]] = {}
    for user in users:
        for email_obj in user.get("email_addresses", []):
            email = email_obj.get("email_address", "").lower()
            if not email:
                continue
            email_map.setdefault(email, [])
            if user["id"] not in email_map[email]:
                email_map[email].append(user["id"])
    return email_map


def derive_uni_email(uni_id: str) -> str:
    return f"{uni_id}@qu.edu.sa".lower()
