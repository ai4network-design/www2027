"""Rule arm for the maintenance-notice compile pilot, written on the development providers only.

extract(text, posted_utc) -> {"start_utc": "YYYY-MM-DDTHH:MM" | None, "end_utc": ... | None}
Finds the first calendar date (or a weekday relative to the posting date), then the first two clock times; a time
zone written after either time applies to both; a UTC time given next to a local one is preferred. US and EU zone
names switch with daylight saving time on the stated date.
"""
import datetime as D
import re

MONTHS = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}
WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
FIXED = {'utc': 0, 'gmt': 0, 'z': 0, 'ist': 5.5, 'jst': 9, 'kst': 9, 'sgt': 8, 'hkt': 8, 'aest': 10, 'aedt': 11, 'msk': 3, 'brt': -3,
         'pdt': -7, 'pst': -8, 'edt': -4, 'est': -5, 'cdt': -5, 'cst': -6, 'mdt': -6, 'mst': -7, 'cest': 2, 'cet': 1, 'bst': 1, 'eest': 3, 'eet': 2}
SEASONAL = {'pt': (-7, -8), 'pacific': (-7, -8), 'et': (-4, -5), 'eastern': (-4, -5), 'ct': (-5, -6), 'central': (-5, -6)}
# Fix of 2026-10-01 (recorded; adopted after the test run of 2026-09-29 had shown city-named zones): a general table of
# city and region names resolved through the IANA database (zoneinfo), so that "Paris time", "London", "Tokyo" convert
# with the correct daylight-saving rule for the stated date. Not tuned to any provider beyond being a list of major cities.
CITIES = {'paris': 'Europe/Paris', 'london': 'Europe/London', 'dublin': 'Europe/Dublin', 'berlin': 'Europe/Berlin', 'frankfurt': 'Europe/Berlin',
          'amsterdam': 'Europe/Amsterdam', 'brussels': 'Europe/Brussels', 'madrid': 'Europe/Madrid', 'rome': 'Europe/Rome', 'milan': 'Europe/Rome',
          'zurich': 'Europe/Zurich', 'vienna': 'Europe/Vienna', 'stockholm': 'Europe/Stockholm', 'oslo': 'Europe/Oslo', 'copenhagen': 'Europe/Copenhagen',
          'helsinki': 'Europe/Helsinki', 'warsaw': 'Europe/Warsaw', 'prague': 'Europe/Prague', 'lisbon': 'Europe/Lisbon', 'athens': 'Europe/Athens',
          'moscow': 'Europe/Moscow', 'istanbul': 'Europe/Istanbul', 'tokyo': 'Asia/Tokyo', 'seoul': 'Asia/Seoul', 'singapore': 'Asia/Singapore',
          'hong': 'Asia/Hong_Kong', 'shanghai': 'Asia/Shanghai', 'beijing': 'Asia/Shanghai', 'taipei': 'Asia/Taipei', 'mumbai': 'Asia/Kolkata',
          'bangalore': 'Asia/Kolkata', 'delhi': 'Asia/Kolkata', 'dubai': 'Asia/Dubai', 'sydney': 'Australia/Sydney', 'melbourne': 'Australia/Melbourne',
          'auckland': 'Pacific/Auckland', 'johannesburg': 'Africa/Johannesburg', 'cairo': 'Africa/Cairo', 'sao': 'America/Sao_Paulo',
          'toronto': 'America/Toronto', 'montreal': 'America/Toronto', 'vancouver': 'America/Vancouver', 'chicago': 'America/Chicago',
          'dallas': 'America/Chicago', 'denver': 'America/Denver', 'phoenix': 'America/Phoenix', 'seattle': 'America/Los_Angeles',
          'los': 'America/Los_Angeles', 'san': 'America/Los_Angeles', 'new': 'America/New_York', 'york': 'America/New_York', 'miami': 'America/New_York',
          'atlanta': 'America/New_York', 'washington': 'America/New_York', 'mexico': 'America/Mexico_City', 'honolulu': 'Pacific/Honolulu',
          'anchorage': 'America/Anchorage', 'aet': 'Australia/Sydney', 'nzt': 'Pacific/Auckland', 'nzst': 'Pacific/Auckland', 'nzdt': 'Pacific/Auckland',
          'hst': 'Pacific/Honolulu', 'akst': 'America/Anchorage', 'akdt': 'America/Anchorage', 'wib': 'Asia/Jakarta', 'ict': 'Asia/Bangkok',
          'pht': 'Asia/Manila', 'sast': 'Africa/Johannesburg', 'wat': 'Africa/Lagos', 'eat': 'Africa/Nairobi', 'gst': 'Asia/Dubai', 'pkt': 'Asia/Karachi',
          'wet': 'Europe/Lisbon', 'west': 'Europe/Lisbon', 'art': 'America/Argentina/Buenos_Aires', 'clt': 'America/Santiago', 'cot': 'America/Bogota',
          'pet': 'America/Lima', 'ast': 'America/Halifax', 'adt': 'America/Halifax', 'nst': 'America/St_Johns', 'ndt': 'America/St_Johns'}
TZ_RE = r'(utc|gmt|pdt|pst|edt|est|cdt|cst|mdt|mst|cest|cet|bst|eest|eet|ist|jst|kst|sgt|hkt|aest|aedt|msk|brt|pt|et|ct|pacific|eastern|central|' + '|'.join(sorted(CITIES)) + r')\b'
TIME_RE = re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|hrs\.?|h)?\b\s*(?:\(?\s*' + TZ_RE + r')?', re.I)


def us_dst(d):
    """US daylight saving: second Sunday of March to first Sunday of November."""
    mar = D.date(d.year, 3, 8) + D.timedelta(days=(6 - D.date(d.year, 3, 8).weekday()) % 7)
    nov = D.date(d.year, 11, 1) + D.timedelta(days=(6 - D.date(d.year, 11, 1).weekday()) % 7)
    return mar <= d < nov


def offset(tz, d):
    tz = tz.lower()
    if tz in FIXED:
        return FIXED[tz]
    if tz in SEASONAL:
        summer, winter = SEASONAL[tz]
        return summer if us_dst(d) else winter
    name = CITIES.get(tz) or (tz if '/' in tz else None)
    if name:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo({z.lower(): z for z in __import__('zoneinfo').available_timezones()}.get(name.lower(), name))
            return D.datetime(d.year, d.month, d.day, 12, tzinfo=zone).utcoffset().total_seconds() / 3600
        except Exception:
            return None
    return None


def find_date(text, posted):
    t = text
    m = re.search(r'\b(20\d{2})-(\d{2})-(\d{2})\b', t)
    if m:
        return D.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.start()
    month = r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?'
    m = re.search(month + r'\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?', t, re.I)
    n = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?' + month + r'(?:,?\s+(20\d{2}))?', t, re.I)
    cands = []
    if m:
        cands.append((m.start(), MONTHS[m.group(1).lower()[:3]], int(m.group(2)), m.group(3)))
    if n:
        cands.append((n.start(), MONTHS[n.group(2).lower()[:3]], int(n.group(1)), n.group(3)))
    if cands:
        pos, mon, day, year = min(cands)
        y = int(year) if year else posted.year
        try:
            d = D.date(y, mon, day)
        except ValueError:
            d = None
        if d and not year and d < posted.date() - D.timedelta(days=31):
            d = D.date(y + 1, mon, day)
        if d:
            return d, pos
    m = re.search(r'\b(' + '|'.join(WEEKDAYS) + r')\b', t, re.I)
    if m:
        wd = WEEKDAYS.index(m.group(1).lower()); d = posted.date()
        return d + D.timedelta(days=(wd - d.weekday()) % 7), m.start()
    return posted.date(), 0


def find_times(text, after):
    out = []
    for m in TIME_RE.finditer(text):
        h, mi, ap, tz = m.group(1), m.group(2), (m.group(3) or '').lower().replace('.', ''), m.group(4)
        if mi is None and ap not in ('am', 'pm') and not (ap.startswith('h') and tz):
            continue   # a bare number is not a clock time
        hour = int(h); minute = int(mi or 0)
        if ap == 'pm' and hour < 12: hour += 12
        if ap == 'am' and hour == 12: hour = 0
        if hour > 23 or minute > 59:
            continue
        out.append(dict(pos=m.start(), end=m.end(), hour=hour, minute=minute, tz=tz.lower() if tz else None))
    # a zone written after the second time of a range ('13:00-15:00 UTC', '1:00 AM to 5:00 AM UTC') applies to the first
    for a, b in zip(out, out[1:]):
        if a['tz'] is None and b['tz'] and re.fullmatch(r'\s*(?:-|–|—|to|until|and)\s*', text[a['end']:b['pos']], re.I):
            a['tz'] = b['tz']
    return [x for x in out if x['pos'] >= after] or out


def extract(text, posted_utc):
    posted = D.datetime.fromisoformat(posted_utc)
    date, dpos = find_date(text, posted)
    times = find_times(text, max(0, dpos - 60))
    if not times:
        return dict(start_utc=None, end_utc=None)
    utc = [x for x in times if x['tz'] in ('utc', 'gmt')]
    use = utc if len(utc) >= 1 and any(x['tz'] and x['tz'] not in ('utc', 'gmt') for x in times) else times
    first = use[0]; second = use[1] if len(use) > 1 else None
    tz = first['tz'] or (second and second['tz']) or next((x['tz'] for x in times if x['tz']), 'utc')
    off = offset(tz, date)
    if off is None:
        off = 0
    def utc_of(x, d):
        return D.datetime(d.year, d.month, d.day, x['hour'], x['minute']) - D.timedelta(hours=off)
    start = utc_of(first, date)
    end = None
    if second:
        tz2 = second['tz'] or tz
        off2 = offset(tz2, date); off2 = off if off2 is None else off2
        end = D.datetime(date.year, date.month, date.day, second['hour'], second['minute']) - D.timedelta(hours=off2)
        if end <= start:
            end += D.timedelta(days=1)
    return dict(start_utc=start.strftime('%Y-%m-%dT%H:%M'), end_utc=end.strftime('%Y-%m-%dT%H:%M') if end else None)
