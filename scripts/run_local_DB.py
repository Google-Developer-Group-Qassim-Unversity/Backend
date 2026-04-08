import argparse
import os
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse, unquote

CONTAINER_NAME = "scores-local"
IMAGE = "mysql:8.0"
HOST_PORT = 3306
DB_USER = "root"
DB_PASSWORD = "rootpassword"
DB_NAME = "scores-local"
VOLUME_NAME = "scores-local-data"

LOCAL_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:{HOST_PORT}/{DB_NAME}"

INFISICAL_PATH = "/admin-backend"
INFISICAL_ENV = "dev"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def container_exists() -> bool:
    r = run(["docker", "ps", "-a", "-q", "--filter", f"name=^{CONTAINER_NAME}$"])
    return bool(r.stdout.strip())


def container_is_running() -> bool:
    r = run(["docker", "ps", "-q", "--filter", f"name=^{CONTAINER_NAME}$"])
    return bool(r.stdout.strip())


def create_container():
    print(f"[setup] Creating container '{CONTAINER_NAME}'...")
    r = run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER_NAME,
            "-p",
            f"{HOST_PORT}:3306",
            "-e",
            f"MYSQL_ROOT_PASSWORD={DB_PASSWORD}",
            "-e",
            f"MYSQL_DATABASE={DB_NAME}",
            "-v",
            f"{VOLUME_NAME}:/var/lib/mysql",
            "--network",
            "host",
            IMAGE,
        ]
    )
    if r.returncode != 0:
        print(f"[error] Failed to create container:\n{r.stderr}")
        sys.exit(1)
    print(f"[setup] Container '{CONTAINER_NAME}' created.")


def start_container():
    print(f"[setup] Starting container '{CONTAINER_NAME}'...")
    r = run(["docker", "start", CONTAINER_NAME])
    if r.returncode != 0:
        print(f"[error] Failed to start container:\n{r.stderr}")
        sys.exit(1)
    print(f"[setup] Container '{CONTAINER_NAME}' started.")


def wait_for_mysql(max_retries: int = 30, delay: float = 1.0):
    print("[setup] Waiting for MySQL to be ready...")
    for attempt in range(1, max_retries + 1):
        r = run(
            [
                "docker",
                "exec",
                CONTAINER_NAME,
                "mysqladmin",
                "ping",
                "-h",
                "localhost",
                "-u",
                DB_USER,
                f"-p{DB_PASSWORD}",
            ]
        )
        if r.returncode == 0:
            r2 = run(["docker", "exec", CONTAINER_NAME, "mysql", "-u", DB_USER, f"-p{DB_PASSWORD}", "-e", "SELECT 1"])
            if r2.returncode == 0:
                print(f"[setup] MySQL is ready (attempt {attempt}/{max_retries}).")
                return
        time.sleep(delay)
    print(f"[error] MySQL did not become ready after {max_retries} attempts.")
    sys.exit(1)


def run_migrations():
    print("[setup] Running Alembic migrations...")
    r = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"], env={**os.environ, "DATABASE_URL": LOCAL_DATABASE_URL}
    )
    if r.returncode != 0:
        print("[error] Migrations failed.")
        sys.exit(1)
    print("[setup] Migrations complete.")


def get_prod_db_url() -> str:
    print("[infisical] Fetching DATABASE_URL...")
    r = run(
        [
            "infisical",
            "secrets",
            "get",
            "DATABASE_URL",
            "--path",
            INFISICAL_PATH,
            "--env",
            INFISICAL_ENV,
            "--silent",
            "--plain",
        ]
    )
    if r.returncode != 0 or not r.stdout.strip():
        print("[error] Failed to fetch DATABASE_URL from infisical.")
        print("        Make sure you are logged in: infisical login")
        sys.exit(1)

    url = r.stdout.strip().split("\n")[0].strip()

    if not url.startswith("mysql"):
        print("[error] Unexpected value received for DATABASE_URL.")
        sys.exit(1)

    return url


def parse_db_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "",
        "port": str(parsed.port or 3306),
        "user": parsed.username or "",
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
    }


def strip_definer(data: bytes) -> bytes:
    import re

    pattern = rb"""DEFINER\s*=\s*`[^`]*`\s*@\s*`[^`]*`\s*"""
    return re.sub(pattern, b"", data)


def dump_prod_to_local(prod_url: str):
    prod = parse_db_url(prod_url)

    print(f"[dump] Recreating local database '{DB_NAME}'...")
    r = run(
        [
            "docker",
            "exec",
            "-e",
            f"MYSQL_PWD={DB_PASSWORD}",
            CONTAINER_NAME,
            "mysql",
            "-u",
            DB_USER,
            "-e",
            f"DROP DATABASE IF EXISTS `{DB_NAME}`; CREATE DATABASE `{DB_NAME}`;",
        ]
    )
    if r.returncode != 0:
        print("[error] Failed to recreate local database.")
        sys.exit(1)

    r = run(
        [
            "docker",
            "exec",
            "-e",
            f"MYSQL_PWD={DB_PASSWORD}",
            CONTAINER_NAME,
            "mysql",
            "-u",
            DB_USER,
            "-e",
            f"USE `{DB_NAME}`;",
        ]
    )
    if r.returncode != 0:
        print("[error] Recreated database is not usable.")
        sys.exit(1)

    print(f"[dump] Dumping from prod ({prod['host']}) to local (this may take a moment)...")

    dump_proc = subprocess.Popen(
        [
            "docker",
            "exec",
            "-e",
            f"MYSQL_PWD={prod['password']}",
            CONTAINER_NAME,
            "mysqldump",
            "-h",
            prod["host"],
            "-P",
            prod["port"],
            "-u",
            prod["user"],
            "--no-tablespaces",
            "--skip-lock-tables",
            "--set-gtid-purged=OFF",
            "--routines",
            "--triggers",
            prod["database"],
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert dump_proc.stdout is not None
    assert dump_proc.stderr is not None

    dump_output = dump_proc.stdout.read()
    dump_stderr = dump_proc.stderr.read().decode()
    dump_proc.wait()

    if dump_proc.returncode not in (0, -13):
        print(f"[error] mysqldump failed (exit code {dump_proc.returncode}).")
        if "Access denied" in dump_stderr:
            print("        The prod database rejected the credentials.")
        else:
            for line in dump_stderr.strip().split("\n"):
                print(f"        {line}")
        sys.exit(1)

    print("[dump] Stripping DEFINER clauses...")
    dump_output = strip_definer(dump_output)

    restore_proc = subprocess.Popen(
        ["docker", "exec", "-e", f"MYSQL_PWD={DB_PASSWORD}", "-i", CONTAINER_NAME, "mysql", "-u", DB_USER, DB_NAME],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    _, restore_stderr_raw = restore_proc.communicate(dump_output)
    restore_stderr = restore_stderr_raw.decode()

    if restore_proc.returncode != 0:
        print(f"[error] mysql restore failed (exit code {restore_proc.returncode}).")
        if restore_stderr.strip():
            for line in restore_stderr.strip().split("\n"):
                print(f"        {line}")
        sys.exit(1)

    print("[dump] Production data copied to local database.")


def remove_container():
    print(f"[reset] Stopping and removing container '{CONTAINER_NAME}'...")
    run(["docker", "rm", "-f", CONTAINER_NAME])
    print(f"[reset] Removing volume '{VOLUME_NAME}'...")
    run(["docker", "volume", "rm", VOLUME_NAME])
    print("[reset] Done.")


def main():
    parser = argparse.ArgumentParser(description="Set up a local MySQL dev database with optional prod data sync.")
    parser.add_argument(
        "--schema-only", action="store_true", help="Only run schema migrations via Alembic (no prod data copy)."
    )
    parser.add_argument(
        "--reset", action="store_true", help="Stop and remove the container and its volume before starting fresh."
    )
    args = parser.parse_args()

    if not shutil.which("docker"):
        print("[error] docker is not installed or not on PATH.", file=sys.stderr)
        print("        Install: https://docs.docker.com/get-docker/", file=sys.stderr)
        sys.exit(1)

    if args.reset:
        remove_container()

    if container_is_running():
        print(f"[setup] Container '{CONTAINER_NAME}' is already running.")
    elif container_exists():
        start_container()
    else:
        create_container()

    wait_for_mysql()

    if args.schema_only:
        run_migrations()
    else:
        print()
        response = input("  This will REPLACE all local data with a copy of production.\n  Continue? [y/N] ")
        if response.strip().lower() != "y":
            print("Aborted.")
            return

        prod_url = get_prod_db_url()
        dump_prod_to_local(prod_url)

    print(f"\n[done] Database URL: {LOCAL_DATABASE_URL}")


if __name__ == "__main__":
    main()
