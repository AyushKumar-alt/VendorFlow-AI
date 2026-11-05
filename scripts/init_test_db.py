"""Initialize dedicated test database for VendorFlow AI.

CRITICAL SAFETY RULE:
- Only creates the database named exactly 'vendorflow_test' if it does not exist.
- Never drops, alters, or touches the development database 'vendorflow'.
- Never prints passwords or credentials.
"""

import asyncio
import sys
from pathlib import Path

# Add apps/api to path to import app modules if running from project root
apps_api_path = Path(__file__).resolve().parents[1] / "apps" / "api"
if str(apps_api_path) not in sys.path:
    sys.path.insert(0, str(apps_api_path))

import asyncpg

from app.core.config import settings


async def init_test_database() -> None:
    test_url = settings.get_test_database_url()

    # Safety assertion
    if test_url.database != "vendorflow_test":
        print(
            f"ERROR: Safety violation. Target database '{test_url.database}' is not 'vendorflow_test'.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Connecting to PostgreSQL host {settings.postgres_host}:{settings.postgres_port}..."
    )
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,  # Maintenance connection to dev db
    )

    try:
        row = await conn.fetchrow(
            "SELECT 1 FROM pg_database WHERE datname = 'vendorflow_test'"
        )
        if row:
            print("Database 'vendorflow_test' already exists.")
        else:
            print("Creating database 'vendorflow_test'...")
            # CREATE DATABASE cannot run inside a transaction block
            await conn.execute("CREATE DATABASE vendorflow_test OWNER vendorflow")
            print("Database 'vendorflow_test' successfully created.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(init_test_database())
