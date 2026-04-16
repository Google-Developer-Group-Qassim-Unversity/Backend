from fastapi import APIRouter, HTTPException, status, Depends
from app.DB import members as member_queries
from sqlalchemy import text
from app.DB.main import SessionLocal, engine
from app.routers.models import Member_model
import os
from time import perf_counter
from json import dumps

router = APIRouter()


@router.get(
    "", status_code=status.HTTP_200_OK, description="Basic health check endpoint to verify that the API is running."
)
def health_check():
    return {"status": "ok"}


@router.get(
    "/db",
    status_code=status.HTTP_200_OK,
    description="Check database connectivity and print to the console various debugstatus values, such as connection pool status and MySQL global status/variables as well as query performance metrics.",
)
def db_check():
    with SessionLocal() as session:
        times = []
        status_rows = None
        var_rows = None
        try:
            for _ in range(10):
                t0 = perf_counter()
                # Try a simple query to check database connectivity
                session.execute(text("SELECT 1"))
                t1 = perf_counter()
                delta_ms = (t1 - t0) * 1000
                times.append(delta_ms)
            status_rows = session.execute(
                text(
                    "SHOW GLOBAL STATUS WHERE Variable_name IN "
                    "('Threads_connected','Threads_running','Max_used_connections','Aborted_clients','Aborted_connects','Connections')"
                )
            ).all()
            var_rows = session.execute(
                text(
                    "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('max_connections','wait_timeout','connect_timeout')"
                )
            ).all()
            return {"database": "connected"}
        except Exception as e:
            print(f"Database connection error: {e}")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database connection failed")
        finally:
            print(
                f"\n{'=' * 50}\n",
                dumps(
                    {
                        "======== Pool Status ========": "",
                        # pool_size: The configured “base” number of connections the pool tries to maintain (5 in your case).
                        # Connections in pool / checked_in: Connections that are currently idle/available in the pool (ready to be reused).
                        # Current Overflow / overflow: How many connections currently exist beyond pool_size (in practice: it’s effectively tied to “how far the total open connections are from pool_size”).
                        # Current Checked out connections / checked_out: Connections that are in use (leased to your requests/threads). Those are not available until returned.
                        "pool_status": engine.pool.status(),
                        "pool_size": engine.pool.size(),
                        "checked_in": engine.pool.checkedin(),
                        "checked_out": engine.pool.checkedout(),
                        "overflow": engine.pool.overflow(),
                        "======== MySQL Status ========": "",
                        "DB_name": engine.url.database,
                        "DB_host": engine.url.host,
                        "MySQL_status": {row.Variable_name: row.Value for row in status_rows} if status_rows else None,
                        "MySQL_variables": {row.Variable_name: row.Value for row in var_rows} if var_rows else None,
                        "======== Query Times ========": "",
                        "average_query_time_ms": f"{int(sum(times) / len(times))}" if times else None,
                        "all_query_times_ms": [f"{int(t)}" for t in times] if times else [],
                        "pid": os.getpid(),
                    },
                    indent=4,
                ),
                f"\n{'=' * 50}\n",
            )


@router.get(
    "/print-status",
    status_code=status.HTTP_200_OK,
    description="DEBUGGING ENDPOINT - Prints the current database connection pool status to the console. Useful for diagnosing connection leaks or pool exhaustion issues.",
)
def print_pool_status():
    print(
        dumps(
            {
                "======== Pool Status ========": "",
                # pool_size: The configured “base” number of connections the pool tries to maintain (5 in your case).
                # Connections in pool / checked_in: Connections that are currently idle/available in the pool (ready to be reused).
                # Current Overflow / overflow: How many connections currently exist beyond pool_size (in practice: it’s effectively tied to “how far the total open connections are from pool_size”).
                # Current Checked out connections / checked_out: Connections that are in use (leased to your requests/threads). Those are not available until returned.
                "pool_status": engine.pool.status(),
                "pool_size": engine.pool.size(),
                "checked_in": engine.pool.checkedin(),
                "checked_out": engine.pool.checkedout(),
                "overflow": engine.pool.overflow(),
            },
            indent=4,
        )
    )
