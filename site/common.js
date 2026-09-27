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

// Groups ordered by their earliest concert, so output reads like the tour route.
function orderedGroups(concerts, keyFn) {
  return [...groupBy(concerts, keyFn)].sort((a, b) =>
    a[1][0].dateKey.localeCompare(b[1][0].dateKey) || a[0].localeCompare(b[0]));
}

// Plain-text export: Country > City > dated concerts, readable in any text editor.
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const exportDate = { format: d =>
  `${WEEKDAYS[d.getDay()]} ${String(d.getDate()).padStart(2, "0")} ${MONTHS[d.getMonth()]} ${d.getFullYear()}` };

function artistExportText(name, concerts, note = "") {
  const sorted = [...concerts].sort((a, b) => a.dateKey.localeCompare(b.dateKey));
  const s = summarize(sorted);
  const lines = [
    name,
    "=".repeat(Math.max(name.length, 20)),
    `${plural(s.countries, "country", "countries")} · ${plural(s.cities, "city", "cities")} · ${plural(s.concerts, "concert")} · ${dateRange(s)}`,
  ];
  if (note) lines.push(`(${note})`);
  for (const [country, inCountry] of orderedGroups(sorted, c => c.Country)) {
    lines.push("", (country || "Unknown country").toUpperCase());
    for (const [city, inCity] of orderedGroups(inCountry, c => c.City)) {
      lines.push(`  ${city}`);
      for (const c of inCity) {
        const festival = c.Festival ? `  (${c.Festival})` : "";
        lines.push(`    ${exportDate.format(c.dateObj)}   ${c.Venue || "Unknown venue"}${festival}`);
        if (c.Link) lines.push(`      ${c.Link}`);
      }
    }
  }
  return lines.join("\n");
}

// artists: array of [name, concerts, note?] entries.
function downloadExport(artists, filename, header = "") {
  const body = artists.map(([name, concerts, note]) => artistExportText(name, concerts, note)).join("\n\n\n");
  const text = (header ? `${header}\n\n\n` : "") + body + "\n";
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const safeFilename = name => name.replace(/[\\/:*?"<>|]+/g, "_").trim() || "artist";

// Applies a country filter to [name, concerts] pairs. With no countries chosen, everything
// is kept. An artist with no concerts in the chosen countries either keeps all concerts
// (keepAllIfNone) or is dropped.
function filterByCountries(artists, countries, keepAllIfNone) {
  if (!countries.size) return { kept: artists.map(([n, c]) => [n, c]), fallback: 0, dropped: 0 };
  const kept = [];
  let fallback = 0, dropped = 0;
  for (const [name, concerts] of artists) {
    const matching = concerts.filter(c => countries.has(c.Country));
    if (matching.length) kept.push([name, matching]);
    else if (keepAllIfNone) {
      fallback++;
      kept.push([name, concerts, "No concerts in the selected countries, so all concerts are listed"]);
    } else dropped++;
  }
  return { kept, fallback, dropped };
}

const KEEP_ALL_KEY = "exportKeepAllIfNone";

// Export dialog: pick countries (none = all) and what to do with artists outside them.
// artists: [name, concerts] pairs; baseName: filename without ".txt".
function openExportDialog(artists, baseName) {
  const counts = new Map();
  for (const [, concerts] of artists)
    for (const c of concerts) counts.set(c.Country, (counts.get(c.Country) || 0) + 1);
  const countries = [...counts.keys()].sort((a, b) => a.localeCompare(b));
  let keepAll = false;
  try { keepAll = localStorage.getItem(KEEP_ALL_KEY) === "1"; } catch (e) {}

  const dialog = document.createElement("dialog");
  dialog.className = "export-dialog";
  dialog.innerHTML = `
    <form method="dialog">
      <h2>Export ${artists.length === 1 ? escapeHtml(artists[0][0]) : plural(artists.length, "artist")}</h2>
      <fieldset>
        <legend>Countries</legend>
        <p class="hint">Only concerts in the ticked countries are exported. Leave all unticked to export every country.</p>
        <div class="country-grid">${countries.map(country => `
          <label><input type="checkbox" value="${escapeHtml(country)}">
            <span>${escapeHtml(country || "Unknown country")}</span>
            <span class="count">${counts.get(country)}</span></label>`).join("")}
        </div>
        <button type="button" class="clear-countries">Clear countries</button>
      </fieldset>
      <label class="toggle keep-all"><input type="checkbox"${keepAll ? " checked" : ""}>
        If an artist has no concerts in the ticked countries, export all their concerts</label>
      <p class="export-summary" aria-live="polite"></p>
      <div class="dialog-actions">
        <button value="cancel">Cancel</button>
        <button value="export" class="primary">Export</button>
      </div>
    </form>`;
  document.body.append(dialog);

  const boxes = [...dialog.querySelectorAll(".country-grid input")];
  const keepAllBox = dialog.querySelector(".keep-all input");
  const summary = dialog.querySelector(".export-summary");
  const exportButton = dialog.querySelector('button[value="export"]');
  const chosen = () => new Set(boxes.filter(b => b.checked).map(b => b.value));

  function update() {
    const selected = chosen();
    const { kept, fallback, dropped } = filterByCountries(artists, selected, keepAllBox.checked);
    keepAllBox.closest("label").classList.toggle("inactive", !selected.size);
    const total = kept.reduce((n, [, c]) => n + c.length, 0);
    const parts = [`${plural(total, "concert")} from ${plural(kept.length, "artist")}`];
    if (fallback) parts.push(`${plural(fallback, "artist")} with no matches will include all concerts`);
    if (dropped) parts.push(`${plural(dropped, "artist")} skipped (no concerts in the ticked countries)`);
    summary.textContent = kept.length ? `Will export ${parts.join(" · ")}.` : "Nothing to export: no concerts in the ticked countries.";
    exportButton.disabled = !kept.length;
  }

  dialog.addEventListener("change", update);
  dialog.querySelector(".clear-countries").addEventListener("click", () => {
    boxes.forEach(b => { b.checked = false; });
    update();
  });
  dialog.addEventListener("close", () => {
    try { localStorage.setItem(KEEP_ALL_KEY, keepAllBox.checked ? "1" : "0"); } catch (e) {}
    if (dialog.returnValue === "export") {
      const selected = chosen();
      const { kept } = filterByCountries(artists, selected, keepAllBox.checked);
      const names = [...selected].map(c => c || "Unknown country");
      const suffix = !names.length ? "" : names.length <= 3 ? ` (${names.join(", ")})` : ` (${names.length} countries)`;
      const header = names.length ? `Countries: ${names.join(", ")}` : "";
      downloadExport(kept, `${safeFilename(baseName + suffix)}.txt`, header);
    }
    dialog.remove();
  });
  update();
  dialog.showModal();
}
