"""Import past semesters' club structure from CSV files.

    uv run python scripts/import_club_structure.py 461.csv 462.csv          # dry run
    uv run python scripts/import_club_structure.py 461.csv 462.csv --apply  # commit

The file format and the rules are in app/services/club_import.py. Every file
is checked first; any problem in any file means nothing is imported. Against
production, run it through infisical:

    infisical run --env=prod --path=/admin-backend -- uv run python scripts/import_club_structure.py ...
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.DB.main import db_session  # noqa: E402
from app.services.club_import import ImportFailed, import_structure, read_csv  # noqa: E402


def main(paths: list[Path], apply: bool) -> int:
    try:
        rows = [row for path in paths for row in read_csv(path)]
    except ImportFailed as exc:
        print(f"Cannot read the file: {exc}", file=sys.stderr)
        return 1

    actor = "import:" + ",".join(path.name for path in paths)
    with db_session() as session:
        try:
            report = import_structure(session, rows, actor=actor[:255])
        except ImportFailed as exc:
            session.rollback()
            print(f"Nothing imported. {exc}", file=sys.stderr)
            return 1
        print("\n".join(report.lines()))
        if apply:
            session.commit()
            print("\nApplied.")
        else:
            session.rollback()
            print("\nDry run: nothing committed. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import past semesters' club structure from CSV files")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--apply", action="store_true", help="Commit instead of only checking and printing")
    args = parser.parse_args()
    sys.exit(main(args.files, args.apply))
