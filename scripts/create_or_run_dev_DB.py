import subprocess
import sys
import time

CONTAINER_NAME = "scores-local"
IMAGE = "mysql:8.0"
HOST_PORT = 3306
DB_USER = "root"
DB_PASSWORD = "rootpassword"
DB_NAME = "scores-local"
VOLUME_NAME = "scores-local-data"

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:{HOST_PORT}/{DB_NAME}"


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
    r = run([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"{HOST_PORT}:3306",
        "-e", f"MYSQL_ROOT_PASSWORD={DB_PASSWORD}",
        "-e", f"MYSQL_DATABASE={DB_NAME}",
        "-v", f"{VOLUME_NAME}:/var/lib/mysql",
        IMAGE,
    ])
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
        r = run([
            "docker", "exec", CONTAINER_NAME,
            "mysqladmin", "ping", "-h", "localhost",
            "-u", DB_USER, f"-p{DB_PASSWORD}",
        ])
        if r.returncode == 0:
            run([
                "docker", "exec", CONTAINER_NAME,
                "mysql", "-u", DB_USER, f"-p{DB_PASSWORD}", "-e", "SELECT 1",
            ])
            if r.returncode == 0:
                print(f"[setup] MySQL is ready (attempt {attempt}/{max_retries}).")
                return
        time.sleep(delay)
    print(f"[error] MySQL did not become ready after {max_retries} attempts.")
    sys.exit(1)


def run_migrations():
    print("[setup] Running Alembic migrations...")
    r = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env={**__import__("os").environ, "DATABASE_URL": DATABASE_URL},
    )
    if r.returncode != 0:
        print("[error] Migrations failed.")
        sys.exit(1)
    print("[setup] Migrations complete.")


def main():
    if container_is_running():
        print(f"[setup] Container '{CONTAINER_NAME}' is already running.")
    elif container_exists():
        start_container()
    else:
        create_container()

    wait_for_mysql()
    run_migrations()

    print(f"\n[done] Database URL: {DATABASE_URL}")


if __name__ == "__main__":
    main()
