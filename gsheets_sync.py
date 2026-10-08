#!/usr/bin/env python3
"""
gsheets_sync.py
---------------
Standalone CLI to fetch sessions from Google Sheets Apps Script
and write CSV compatible with Command Center's CSV loader.

Usage:
    # Single moderator, today
    python gsheets_sync.py --url "https://script.google.com/macros/s/ABC123/exec" \
        --moderator "Nimith Shetty" --output sessions.csv

    # Specific date
    python gsheets_sync.py --url "..." --moderator "Nimith Shetty" \
        --date 2026-01-09 --output sessions_2026-01-09.csv

    # All moderators for a date (batch)
    python gsheets_sync.py --url "..." --all-moderators \
        --date 2026-01-09 --output-dir ./daily_csv/

    # List moderators for a date
    python gsheets_sync.py --url "..." --list-moderators --date 2026-01-09
"""

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from gsheets_api_client import GSheetsAPIClient, GSheetsError


CSV_COLUMNS = [
    'Type', 'Meeting Link', 'Program Name', 'Session Topic / Name',
    'Moderator Name', 'Session Start Time', 'Session End Time',
    'Meeting Code', 'Host Email', 'Host Password',
    'Prism Type', 'Prism Session ID', 'Prism Session Group ID'
]


def parse_date(s: str) -> date:
    """Parse YYYY-MM-DD string to date."""
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        raise argparse.ArgumentTypeError(f'Invalid date format: {s} (expected YYYY-MM-DD)')


def write_csv(sessions: List[dict], path: Path):
    """Write sessions to CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(sessions)
    print(f'✅ Written {len(sessions)} sessions to {path}')


def fetch_and_write(client: GSheetsAPIClient, moderator: str, target_date: date, 
                    output: Path, verbose: bool = False) -> int:
    """Fetch sessions for one moderator and write to CSV."""
    try:
        if verbose:
            print(f'Fetching sessions for "{moderator}" on {target_date.isoformat()}...')
        sessions = client.fetch_sessions(moderator, target_date)
        if verbose:
            print(f'  Found {len(sessions)} sessions')
        write_csv(sessions, output)
        return len(sessions)
    except GSheetsError as e:
        if e.code == 'NO_SESSIONS':
            print(f'⚠️  No sessions for "{moderator}" on {target_date.isoformat()}')
            write_csv([], output)
            return 0
        print(f'❌ Error for "{moderator}": [{e.code}] {e.message}')
        raise


def main():
    parser = argparse.ArgumentParser(
        description='Sync sessions from Google Sheets to CSV for Command Center',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Required
    parser.add_argument('--url', required=True, help='Apps Script Web App URL')
    
    # Date
    parser.add_argument('--date', type=parse_date, default=date.today(),
                        help='Target date (YYYY-MM-DD), default: today')
    
    # Output
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument('--output', type=Path, help='Output CSV file path')
    output_group.add_argument('--output-dir', type=Path, help='Output directory (for --all-moderators)')
    
    # Moderator selection
    moderator_group = parser.add_mutually_exclusive_group()
    moderator_group.add_argument('--moderator', help='Single moderator name')
    moderator_group.add_argument('--all-moderators', action='store_true',
                                 help='Fetch for all moderators with sessions that day')
    moderator_group.add_argument('--list-moderators', action='store_true',
                                 help='List moderators with sessions and exit')
    
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--timeout', type=int, default=30, help='Request timeout seconds')
    
    args = parser.parse_args()
    
    # Validate
    if args.all_moderators and not args.output_dir:
        parser.error('--all-moderators requires --output-dir')
    if args.moderator and not args.output:
        parser.error('--moderator requires --output')
    
    client = GSheetsAPIClient(args.url, timeout=args.timeout)
    
    try:
        if args.list_moderators:
            mods = client.fetch_moderators(args.date)
            if mods:
                print(f'Moderators with sessions on {args.date.isoformat()}:')
                for m in mods:
                    print(f'  - {m}')
            else:
                print(f'No moderators found for {args.date.isoformat()}')
            return 0
        
        if args.all_moderators:
            mods = client.fetch_moderators(args.date)
            if not mods:
                print(f'No moderators found for {args.date.isoformat()}')
                return 0
            
            print(f'Fetching for {len(mods)} moderators...')
            total = 0
            for mod in mods:
                out_file = args.output_dir / f'sessions_{mod.replace(" ", "_")}_{args.date.isoformat()}.csv'
                try:
                    n = fetch_and_write(client, mod, args.date, out_file, args.verbose)
                    total += n
                except GSheetsError:
                    pass  # Already printed
            print(f'✅ Done. Total sessions: {total}')
            return 0
        
        # Single moderator
        if not args.moderator:
            parser.error('Either --moderator or --all-moderators required')
        
        fetch_and_write(client, args.moderator, args.date, args.output, args.verbose)
        return 0
        
    except GSheetsError as e:
        print(f'❌ Fatal: [{e.code}] {e.message}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\n⚠️  Interrupted', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())