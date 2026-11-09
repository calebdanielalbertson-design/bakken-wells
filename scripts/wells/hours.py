"""Should this run wait before it downloads from DMR? Downloads from NDIC and DMR stay outside Central business hours.

    python scripts/wells/hours.py --github-output "$GITHUB_OUTPUT"            # defer=true during business hours
    python scripts/wells/hours.py --override true --github-output ...         # a manual run that chose to go ahead
    python scripts/wells/hours.py --now 2026-11-09T15:00:00Z                  # judge a given UTC time (tests)

Business hours are 07:00 to 18:00 Central, Monday to Friday. NDIC's website user agreement warns against
"excessive mining of data during normal business hours", and the project keeps every NDIC and DMR download
outside them. GitHub may start a scheduled run hours late (the 10:27 UTC run has started around 16:30 UTC),
so the time is checked when the run actually starts, not when it was scheduled. A deferred run downloads
nothing; the evening schedule picks the work up.

Central time is computed from the US daylight-saving rule (second Sunday of March to first Sunday of
November), so the script needs no time-zone database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

START_HOUR, END_HOUR = 7, 18


def central(now_utc: dt.datetime) -> dt.datetime:
    """US Central wall-clock time (naive) for an aware UTC time."""
    year = now_utc.year

    def nth_sunday(month: int, n: int) -> dt.date:
        first = dt.date(year, month, 1)
        return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    # Daylight time runs from 02:00 CST on the second Sunday of March (08:00 UTC)
    # to 02:00 CDT on the first Sunday of November (07:00 UTC).
    begins = dt.datetime.combine(nth_sunday(3, 2), dt.time(8), dt.timezone.utc)
    ends = dt.datetime.combine(nth_sunday(11, 1), dt.time(7), dt.timezone.utc)
    offset = -5 if begins <= now_utc < ends else -6
    return (now_utc + dt.timedelta(hours=offset)).replace(tzinfo=None)


def business_hours(local: dt.datetime) -> bool:
    return local.weekday() < 5 and START_HOUR <= local.hour < END_HOUR


def decide(now_utc: dt.datetime, override: bool = False) -> tuple[bool, str]:
    """(defer, message)."""
    local = central(now_utc)
    stamp = local.strftime('%a %Y-%m-%d %H:%M Central')
    if not business_hours(local):
        return False, f'{stamp}: outside business hours; downloading from DMR.'
    if override:
        return False, f'{stamp}: business hours, but this manual run chose to go ahead.'
    return True, (f'{stamp}: inside business hours (07:00-18:00 Central, Monday to Friday); '
                  'not downloading from DMR. The evening schedule will pick this up.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--override', default='false', help="'true' lets a manual run go ahead during business hours")
    parser.add_argument('--now', help='UTC time to judge, ISO 8601 (default: now)')
    parser.add_argument('--github-output', help='append defer=true|false to this file ($GITHUB_OUTPUT)')
    args = parser.parse_args(argv)
    now = dt.datetime.fromisoformat(args.now.replace('Z', '+00:00')) if args.now else dt.datetime.now(dt.timezone.utc)
    defer, message = decide(now, args.override.strip().lower() == 'true')
    print(message)
    if defer:
        print('::notice title=Deferred to the evening run::' + message)
    if args.github_output:
        with open(args.github_output, 'a', encoding='utf-8') as fh:
            fh.write(f"defer={'true' if defer else 'false'}\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
