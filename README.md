# SongKickScraper

YES THIS IS AI GENERATED.
A Songkick scraper plus a static local website for finding touring artists.
The scraper collects upcoming concerts for a list of cities into `concerts.csv`;
the website shows every artist playing two or more of those cities, grouped by
Country → City → Concert, with search, sorting and favourites.

## Setup

Requires Python 3.10+.

```bash
pip install -r requirements.txt
```

## 1. Run the scraper

List your cities in `cities.txt`, one per line (`#` lines are ignored). Add the
country after a comma if a name is ambiguous, e.g. `London, UK`.

```bash
python songkick_scraper.py --cities-file cities.txt
```

Or pass cities directly:

```bash
python songkick_scraper.py Amsterdam Berlin "London, UK" --months 3
```

Options:

| Option | Description |
| --- | --- |
| `--months N` | How far ahead to scrape (default 12) |
| `--delay S` | Seconds between requests (default 3) |
| `--output FILE` | Output CSV (default `concerts.csv`; the site only reads this one) |
| `--refresh-metros` | Rebuild the cached Songkick city index (`metro_index.json`) |

Large cities take several minutes each. Press Ctrl+C to stop early — collected
concerts are still saved.

## 2. Open the website

```bash
python serve.py
```

Your browser opens at <http://localhost:8000/site/>. Keep the terminal open
while browsing. Use `--port 8080` if 8000 is taken, or `--no-browser` to skip
opening a tab. After re-scraping, just reload the page.

Favourites are stored in `favourites.json`, so they survive re-scraping.

> Opening `site/index.html` directly won't work — browsers block local file
> access. Always use `python serve.py`.

See `INSTRUCTIONS.txt` for more detail and troubleshooting.
