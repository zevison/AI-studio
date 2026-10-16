"""Regression of US Eastern timezone on Windows Python without tzdata."""
from __future__ import annotations
import datetime as dt
import sys
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core import us_marketing


def main():
    original = ZoneInfo
    class NoWindowsTzdata:
        def __new__(cls, key):
            raise ZoneInfoNotFoundError('No time zone found with key ' + key)
        @staticmethod
        def from_file(file, *, key=None):
            return original.from_file(file, key=key)
    with patch.object(us_marketing, 'ZoneInfo', NoWindowsTzdata):
        alternative = us_marketing.load_us_eastern_zone()
    cases = [
        (dt.datetime(2026, 3, 8, 6, 59, tzinfo=dt.timezone.utc), -5),
        (dt.datetime(2026, 3, 8, 7, 1, tzinfo=dt.timezone.utc), -4),
        (dt.datetime(2026, 11, 1, 5, 59, tzinfo=dt.timezone.utc), -4),
        (dt.datetime(2026, 11, 1, 6, 1, tzinfo=dt.timezone.utc), -5),
    ]
    for sample, hour in cases:
        assert sample.astimezone(alternative).utcoffset() == dt.timedelta(hours=hour)
    now=dt.datetime(2026,10,12,12,tzinfo=dt.timezone.utc)
    with patch.object(us_marketing, 'US_EAST', alternative):
        assert us_marketing.context(now)['as_of_us']=='2026-10-12'
    print('PASS: Windows without tzdata -> bundled New York TZif, correct 2026 DST, accurate US date')

if __name__ == '__main__':
    main()
