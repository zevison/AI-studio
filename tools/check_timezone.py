"""No-network US market timezone startup/DST smoke check."""
from __future__ import annotations
import datetime as dt
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.us_marketing import US_EAST, context


def main():
    # Modern US spring/fall transitions.  The exact UTC instants matter.
    cases = [
        (dt.datetime(2026, 3, 8, 6, 59, tzinfo=dt.timezone.utc), -5),
        (dt.datetime(2026, 3, 8, 7, 1, tzinfo=dt.timezone.utc), -4),
        (dt.datetime(2026, 11, 1, 5, 59, tzinfo=dt.timezone.utc), -4),
        (dt.datetime(2026, 11, 1, 6, 1, tzinfo=dt.timezone.utc), -5),
    ]
    for instant, expected in cases:
        got = instant.astimezone(US_EAST)
        if got.utcoffset() != dt.timedelta(hours=expected):
            print('[ERROR] 美国夏令时转换不正确:', instant.isoformat(), got.isoformat())
            return 2
    data = context()
    if data['time_zone'] != 'America/New_York' or not data['as_of_us']:
        print('[ERROR] 无法计算美国当地日期')
        return 3
    print('[OK] 美国纽约时区数据正常；2026 年夏令时/冬令时校验通过，日期：'+data['as_of_us'])
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
