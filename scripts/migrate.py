"""Apply ordered SQL migrations to one database.

Usage: python scripts/migrate.py --dsn postgresql://... --dir db/migrations/<db>
Each *.sql file runs once, in lexical order, inside a transaction; applied
filenames are recorded in schema_migrations.
"""
import argparse
import asyncio
import pathlib
import sys

import asyncpg


async def migrate(dsn: str, directory: pathlib.Path) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   filename text PRIMARY KEY,
                   applied_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        applied = {
            r["filename"]
            for r in await conn.fetch("SELECT filename FROM schema_migrations")
        }
        for path in sorted(directory.glob("*.sql")):
            if path.name in applied:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                )
            print(f"applied {path.name}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--dir", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if not args.dir.is_dir():
        sys.exit(f"not a directory: {args.dir}")
    asyncio.run(migrate(args.dsn, args.dir))


if __name__ == "__main__":
    main()
