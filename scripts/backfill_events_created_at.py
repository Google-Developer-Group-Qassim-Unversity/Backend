import sys, argparse
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

from app.DB.main import SessionLocal
from app.DB.schema import Events
from sqlalchemy import select


def main(dry_run: bool):
    with SessionLocal() as session:
        events = session.scalars(select(Events)).all()
        updated = 0
        skipped = 0
        for event in events:
            if event.created_at != event.start_datetime:
                print(f"Event {event.id} ({event.name}): {event.created_at} -> {event.start_datetime}")
                if not dry_run:
                    event.created_at = event.start_datetime
                updated += 1
            else:
                skipped += 1

        if not dry_run:
            session.commit()
            print(f"\n{updated} events updated, {skipped} skipped.")
        else:
            session.rollback()
            print(f"\nDry run: {updated} would be updated, {skipped} skipped (no changes applied).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill events.created_at to match start_datetime")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying them")
    args = parser.parse_args()
    main(args.dry_run)
