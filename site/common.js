// Shared helpers for loading and grouping concerts.csv.

const CSV_URL = "../concerts.csv";

function parseCsv(text) {
  // RFC 4180: quoted fields, "" escapes, commas/newlines inside quotes.
  const rows = [];
  let row = [], field = "", inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.some(v => v !== ""));
}

async function loadConcerts() {
  let response;
  try {
    response = await fetch(CSV_URL, { cache: "no-store" });
  } catch (e) {
    throw new Error("Could not reach the local server. Start it with: python serve.py");
  }
  if (!response.ok) throw new Error(`Could not load concerts.csv (HTTP ${response.status}). Run songkick_scraper.py first.`);
  const text = (await response.text()).replace(/^﻿/, "");
  const [header, ...lines] = parseCsv(text);
  const seen = new Set();
  const concerts = [];
  for (const line of lines) {
    const c = Object.fromEntries(header.map((h, i) => [h, (line[i] || "").trim()]));
    const [d, m, y] = c.Date.split("/").map(Number);
    if (!y) continue;
    c.dateObj = new Date(y, m - 1, d);
    c.dateKey = `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const key = `${c.Artist}\u0000${c.Link}\u0000${c.dateKey}`;
    if (seen.has(key)) continue;
    seen.add(key);
    concerts.push(c);
  }
  return concerts;
}

function groupBy(items, keyFn) {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }
  return map;
}

const cityKey = c => `${c.City}|${c.Country}`;

function summarize(concerts) {
  const sorted = [...concerts].sort((a, b) => a.dateKey.localeCompare(b.dateKey));
  return {
    concerts: sorted.length,
    cities: new Set(sorted.map(cityKey)).size,
    countries: new Set(sorted.map(c => c.Country)).size,
    first: sorted[0].Date,
    last: sorted[sorted.length - 1].Date,
  };
}

// An artist is "touring" when they play at least two different cities.
const isTouring = concerts => new Set(concerts.map(cityKey)).size >= 2;

const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;

function dateRange(s) {
  return s.first === s.last ? s.first : `${s.first} – ${s.last}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function foldText(value) {
  return value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// Favourites live in favourites.json (via serve.py), separate from concerts.csv.
const FAVOURITES_URL = "/api/favourites";

async function loadFavourites() {
  const response = await fetch(FAVOURITES_URL, { cache: "no-store" });
  if (!response.ok) throw new Error(`Could not load favourites (HTTP ${response.status})`);
  return new Set(await response.json());
}

async function toggleFavourite(name) {
  // Re-read before writing so changes made in other tabs aren't lost.
  const favourites = await loadFavourites();
  if (favourites.has(name)) favourites.delete(name); else favourites.add(name);
  const response = await fetch(FAVOURITES_URL, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify([...favourites]),
  });
  if (!response.ok) throw new Error(`Could not save favourites (HTTP ${response.status})`);
  return new Set(await response.json());
}

function starButton(name, isFavourite) {
  return `<button class="star${isFavourite ? " on" : ""}" data-artist="${escapeHtml(name)}"
    aria-pressed="${isFavourite}" title="${isFavourite ? "Remove from favourites" : "Add to favourites"}">${isFavourite ? "★" : "☆"}</button>`;
}

function showError(container, message) {
  container.innerHTML = `<div class="error">${escapeHtml(message)}</div>`;
}
