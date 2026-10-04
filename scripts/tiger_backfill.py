"""Safely re-import local history and optionally verify a configured Tiger service."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.tiger_data import integration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sync', action='store_true', help='Connect to TIGER_DATABASE_URL and drain pending events')
    args = parser.parse_args()
    integration.backfill()
    if args.sync:
        if not integration.database_url:
            print('TIGER_DATABASE_URL is not configured; all events remain locally queued.')
            return 2
        try:
            while integration.sync_once() == 100: pass
        except Exception as exc:
            print(f'Tiger sync failed ({type(exc).__name__}); durable events remain queued.')
            return 1
    print(json.dumps(integration.progress(), indent=2))
    return 0


if __name__ == '__main__': raise SystemExit(main())
