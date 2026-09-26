"""Scrape upcoming Songkick concerts for a list of cities into a CSV.

Data comes from the schema.org JSON-LD embedded in Songkick's metro-area
listing pages (not the official API). One CSV row is written per performer
per concert date; multi-day festivals are expanded into one row per day.

Usage:
    python songkick_scraper.py Amsterdam Copenhagen "London, UK"
    python songkick_scraper.py --cities-file cities.txt --months 12 --output concerts.csv
"""

import argparse
import csv
import gzip
import json
import math
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE_URL = "https://www.songkick.com"
# Songkick's CDN rejects spoofed browser User-Agents (HTTP 406) but accepts honest ones.
USER_AGENT = "SongKickScraper/1.0 (personal concert research)"
PAGE_SIZE = 50
MAX_EVENTS_PER_QUERY = 2000  # listings stop paginating at ~41 pages (~2,050 events)
WINDOW_DAYS = 30
FESTIVAL_LOOKBACK_DAYS = 14  # festivals only appear on their start date
METRO_INDEX_PATH = Path(__file__).with_name("metro_index.json")

CSV_COLUMNS = ["Artist", "Country", "City", "Venue", "Venue Address", "Date", "Link", "Festival"]

COUNTRY_NAMES = {"UK": "United Kingdom", "US": "United States"}

# Country segments as they appear in Songkick metro slugs, e.g. "31366-netherlands-amsterdam".
EUROPEAN_COUNTRY_SLUGS = {
    "albania", "andorra", "austria", "belarus", "belgium", "bosnia-and-herzegovina",
    "bosnia-herzegovina", "bulgaria", "croatia", "cyprus", "czech-republic", "czechia",
    "denmark", "estonia", "faroe-islands", "finland", "france", "georgia", "germany",
    "gibraltar", "greece", "hungary", "iceland", "ireland", "italy", "kosovo", "latvia",
    "liechtenstein", "lithuania", "luxembourg", "macedonia", "malta", "moldova", "monaco",
    "montenegro", "netherlands", "north-macedonia", "norway", "poland", "portugal",
    "romania", "russia", "san-marino", "serbia", "slovakia", "slovenia", "spain",
    "sweden", "switzerland", "turkey", "uk", "ukraine",
}

# Common ways people write a country that differ from Songkick's slug.
COUNTRY_ALIASES = {
    "united-kingdom": "uk", "great-britain": "uk", "england": "uk", "scotland": "uk",
    "wales": "uk", "northern-ireland": "uk", "gb": "uk",
    "holland": "netherlands", "the-netherlands": "netherlands", "nl": "netherlands",
    "czechia": "czech-republic", "united-states": "us", "usa": "us",
}

# Characters NFKD doesn't decompose into ASCII.
TRANSLITERATIONS = str.maketrans({"ø": "o", "æ": "ae", "å": "a", "ß": "ss", "ł": "l", "đ": "d", "ı": "i"})


def log(message):
    print(message, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- HTTP

class Fetcher:
    def __init__(self, delay):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})

    def get(self, url, params=None, retries=4):
        for attempt in range(retries + 1):
            time.sleep(self.delay)
            try:
                response = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                error = str(exc)
            else:
                if response.status_code == 200:
                    return response
                if response.status_code == 406:
                    raise RuntimeError(
                        "Songkick returned 406 Not Acceptable - the User-Agent is being rejected."
                    )
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(int(retry_after))
            if attempt == retries:
                raise RuntimeError(f"Giving up on {url} after {retries + 1} attempts ({error})")
            backoff = 5 * 2 ** attempt
            log(f"  {error}; retrying in {backoff}s")
            time.sleep(backoff)


# --------------------------------------------------------------------------- Cities

def slugify(text):
    text = text.strip().lower().translate(TRANSLITERATIONS)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def load_metro_index(fetcher, refresh=False):
    """Return a list of metro slugs like '31366-netherlands-amsterdam', cached on disk."""
    if METRO_INDEX_PATH.exists() and not refresh:
        return json.loads(METRO_INDEX_PATH.read_text(encoding="utf-8"))["metros"]

    log("Building metro-area index from Songkick sitemaps (one-time)...")
    sitemap = fetcher.get(f"{BASE_URL}/sitemap.xml").text
    metro_sitemaps = re.findall(r"<loc>([^<]*/sitemap/metro_areas\.[^<]*)</loc>", sitemap)
    metros = set()
    for url in metro_sitemaps:
        xml = gzip.decompress(fetcher.get(url).content).decode("utf-8")
        metros.update(re.findall(r"/metro-areas/(\d+-[a-z0-9-]+)", xml))
    metros = sorted(metros)
    METRO_INDEX_PATH.write_text(
        json.dumps({"built": date.today().isoformat(), "metros": metros}, indent=0),
        encoding="utf-8",
    )
    log(f"  indexed {len(metros)} metro areas")
    return metros


def resolve_city(query, metros):
    """Map 'Amsterdam' or 'Amsterdam, Netherlands' to a metro slug. Raises ValueError."""
    city_part, _, country_part = query.partition(",")
    city = slugify(city_part)
    country = slugify(country_part)
    country = COUNTRY_ALIASES.get(country, country)
    if not city:
        raise ValueError(f"empty city name in {query!r}")

    # slug = "<id>-<country>-<city>"; strip the id and match the city as a suffix.
    candidates = []
    for metro in metros:
        rest = metro.split("-", 1)[1]
        if rest.endswith("-" + city):
            candidates.append((metro, rest[: -len(city) - 1]))

    if country:
        matches = [m for m, c in candidates if c == country]
    else:
        # No suffix-only fallback: "Palma" must not match "spain-santa-cruz-de-la-palma".
        matches = [m for m, c in candidates if c in EUROPEAN_COUNTRY_SLUGS]

    if len(matches) == 1:
        return matches[0]
    if not matches:
        hint = f" Candidates: {', '.join(m for m, _ in candidates)}" if candidates else ""
        raise ValueError(f"no Songkick metro area found for {query!r}.{hint}")
    raise ValueError(
        f"{query!r} is ambiguous: {', '.join(matches)}. Add a country, e.g. \"{city_part.strip()}, <country>\"."
    )


# --------------------------------------------------------------------------- Scraping

def parse_events(html):
    events = []
    for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError as exc:
            log(f"  skipping unparseable JSON-LD block ({exc})")
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "MusicEvent":
                events.append(item)
    return events


def parse_total(html):
    match = re.search(r"Currently there are <b>([\d,.\s]+)</b>", html)
    return int(re.sub(r"\D", "", match.group(1))) if match else None


def fetch_listing_page(fetcher, metro, start, end, page):
    params = {
        "filters[minDate]": start.strftime("%m/%d/%Y"),
        "filters[maxDate]": end.strftime("%m/%d/%Y"),
        "page": page,
    }
    html = fetcher.get(f"{BASE_URL}/en/metro-areas/{metro}", params=params).text
    return html


def scrape_window(fetcher, metro, start, end):
    """Collect all events in [start, end], splitting the window if it exceeds the pagination cap."""
    html = fetch_listing_page(fetcher, metro, start, end, 1)
    total = parse_total(html)

    if total is not None and total > MAX_EVENTS_PER_QUERY and start < end:
        middle = start + (end - start) // 2
        log(f"  {start}..{end}: {total} events, splitting window")
        return scrape_window(fetcher, metro, start, middle) + scrape_window(
            fetcher, metro, middle + timedelta(days=1), end
        )
    if total is not None and total > MAX_EVENTS_PER_QUERY:
        log(f"  warning: {total} events on {start} exceeds the listing cap; some will be missed")

    events = parse_events(html)
    last_page = math.ceil(total / PAGE_SIZE) if total is not None else None
    page = 1
    while events and (last_page is None or page < last_page):
        page += 1
        page_events = parse_events(fetch_listing_page(fetcher, metro, start, end, page))
        if not page_events:
            break
        events.extend(page_events)
    log(f"  {start}..{end}: {len(events)} events")
    return events


def scrape_metro(fetcher, metro, first_day, last_day):
    events = []
    start = first_day
    while start <= last_day:
        end = min(start + timedelta(days=WINDOW_DAYS - 1), last_day)
        events.extend(scrape_window(fetcher, metro, start, end))
        start = end + timedelta(days=1)
    return events


# --------------------------------------------------------------------------- Rows

def parse_day(value):
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def event_to_rows(event, keep_from, keep_until):
    """Turn one MusicEvent into CSV rows: one per performer per date in range."""
    if "EventCancelled" in (event.get("eventStatus") or ""):
        return []
    start = parse_day(event.get("startDate"))
    if start is None:
        return []
    end = parse_day(event.get("endDate"))
    if end is None or end < start:
        end = start

    link = (event.get("url") or "").split("?")[0].replace("http://", "https://")
    festival = ""
    if "/festivals/" in link:
        festival = (event.get("name") or "").rsplit(" @ ", 1)[0].strip()

    location = event.get("location") or {}
    address = location.get("address") or {}
    country = (address.get("addressCountry") or "").strip()
    street_parts = [address.get("streetAddress"), address.get("postalCode")]

    performers = []
    for performer in event.get("performer") or []:
        name = (performer.get("name") or "").strip()
        if name and name not in performers:
            performers.append(name)

    rows = []
    day = max(start, keep_from)
    while day <= min(end, keep_until):
        for artist in performers:
            rows.append({
                "Artist": artist,
                "Country": COUNTRY_NAMES.get(country, country),
                "City": (address.get("addressLocality") or "").strip(),
                "Venue": (location.get("name") or "").strip(),
                "Venue Address": ", ".join(p.strip() for p in street_parts if p and p.strip()),
                "Date": day,
                "Link": link,
                "Festival": festival,
            })
        day += timedelta(days=1)
    return rows


def write_csv(rows, path):
    unique = {(r["Artist"], r["Link"], r["Date"]): r for r in rows}
    ordered = sorted(unique.values(), key=lambda r: (r["Artist"].casefold(), r["Date"], r["City"]))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in ordered:
            writer.writerow({**row, "Date": row["Date"].strftime("%d/%m/%Y")})
    return len(ordered)


# --------------------------------------------------------------------------- Main

def read_cities(args):
    cities = list(args.cities)
    if args.cities_file:
        for line in Path(args.cities_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                cities.append(line)
    return cities


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")

    parser = argparse.ArgumentParser(description="Scrape upcoming Songkick concerts for cities into a CSV.")
    parser.add_argument("cities", nargs="*", help='city names, e.g. Amsterdam "London, UK"')
    parser.add_argument("--cities-file", help="text file with one city per line")
    parser.add_argument("--months", type=float, default=12, help="how many months ahead to scrape (default 12)")
    parser.add_argument("--delay", type=float, default=3.0, help="seconds between requests (default 3)")
    parser.add_argument("--output", default="concerts.csv", help="CSV output path (default concerts.csv)")
    parser.add_argument("--refresh-metros", action="store_true", help="rebuild the cached metro-area index")
    args = parser.parse_args()

    cities = read_cities(args)
    if not cities:
        parser.error("give at least one city, or --cities-file")

    fetcher = Fetcher(args.delay)
    metros = load_metro_index(fetcher, refresh=args.refresh_metros)

    today = date.today()
    last_day = today + timedelta(days=round(args.months * 30.44))
    first_day = today - timedelta(days=FESTIVAL_LOOKBACK_DAYS)

    rows = []
    summary = []
    try:
        for city in cities:
            try:
                metro = resolve_city(city, metros)
            except ValueError as exc:
                log(f"Skipping {city}: {exc}")
                summary.append((city, "not found", 0, 0))
                continue
            log(f"Scraping {city} ({metro}) from {first_day} to {last_day}")
            events = scrape_metro(fetcher, metro, first_day, last_day)
            city_rows = []
            skipped = 0
            for event in events:
                event_rows = event_to_rows(event, today, last_day)
                last_date = parse_day(event.get("endDate")) or parse_day(event.get("startDate"))
                if not event_rows and last_date and last_date >= today:
                    skipped += 1  # cancelled, or no performers listed
                city_rows.extend(event_rows)
            rows.extend(city_rows)
            summary.append((city, metro, len(events), len(city_rows)))
            log(f"  {city}: {len(events)} events -> {len(city_rows)} rows ({skipped} skipped: cancelled/no performers)")
    except KeyboardInterrupt:
        log("Interrupted - writing rows collected so far.")

    written = write_csv(rows, args.output)
    log("")
    for city, metro, n_events, n_rows in summary:
        log(f"  {city:<25} {metro:<40} {n_events:>6} events {n_rows:>7} rows")
    log(f"Wrote {written} rows to {args.output}")


if __name__ == "__main__":
    main()
