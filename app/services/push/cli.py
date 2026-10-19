"""Send a test push to one member's phones, from a laptop or the server.

    infisical run --env=dev --path=/admin-backend -- uv run python -m app.services.push.cli test --member-id <id>

Reads the same settings as the app (``APNS_*``, ``APPLE_TEAM_ID``,
``DATABASE_URL``), sends straight to APNs without touching the outbox, and
prints one line per phone: the device row's id and Apple's answer. It never
prints a token, the provider token or the key.
"""

import argparse
import asyncio
import sys

from app.config import config
from app.DB import push as push_queries
from app.DB.main import db_session
from app.DB.ids import new_id
from app.services.push import apns, outbox

TEXT = {
    "ar": {"title": "إشعار تجريبي", "body": "وصلك هذا من خادم النادي."},
    "en": {"title": "Test notification", "body": "This came from the club's server."},
}


async def send_test(member_id: int) -> int:
    with db_session() as session:
        devices = push_queries.devices_for(session, [member_id], outbox.device_environment()).get(member_id, [])
        targets = [(device.id, device.token, device.locale.value) for device in devices]
    if not targets:
        sys.stdout.write(
            f"member {member_id}: no phone registered in the {outbox.device_environment().value} environment\n"
        )
        return 1
    failures = 0
    client = apns.get_client()
    try:
        for device_id, token, locale in targets:
            text = TEXT[locale]
            payload = apns.device_payload(
                title=text["title"],
                body=text["body"],
                url="gdgq://notifications",
                kind="broadcast",
                notification_id=new_id(),
                thread_id="news",
            )
            answer = await apns.send(token, payload, priority=10, expiration=0, collapse_id="test", client=client)
            failures += answer.status != 200
            sys.stdout.write(f"device {device_id}: {answer.status} {answer.reason or 'OK'}\n")
    finally:
        await apns.close_client()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.push.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    test = commands.add_parser("test", help="send a test push to a member's phones")
    test.add_argument("--member-id", type=int, required=True)
    args = parser.parse_args(argv)
    if not config.push_enabled:
        sys.stdout.write("push is off: set APNS_KEY_ID and APNS_PRIVATE_KEY (or APNS_PRIVATE_KEY_PATH)\n")
        return 2
    try:
        return asyncio.run(send_test(args.member_id))
    except apns.ApnsNotConfigured as exc:
        sys.stdout.write(f"push is not configured: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
