"""Command-line entry point for the dry-run-first storage migration."""

from __future__ import annotations

import argparse
import asyncio
import json

from src.infra.storage.migration import run_storage_migration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write additive quarantined rows")
    parser.add_argument("--limit", type=int, default=1000, help="maximum legacy records to scan")
    parser.add_argument("--cursor", default=None, help="resume after the previous report cursor")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    report = await run_storage_migration(
        apply=args.apply,
        limit=max(1, min(args.limit, 5000)),
        cursor=args.cursor,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
