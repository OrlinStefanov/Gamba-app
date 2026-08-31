"""A local web app, so the prediction can use real GPS.

`--serve` runs a small stdlib HTTP server on localhost and opens a page that
asks the browser for your position. That is the only way to get an actual beach
fix rather than the city-level guess an IP lookup gives you, and it stays on
your machine: coordinates go from your browser to this process and out to
Open-Meteo, and nowhere else.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import demo as demo_module
from . import model, render, shoreline as shoreline_module, sources
from .fetch import FetchError
from .flags import Flag
from .geo import Location, geocode, validate
from .model import SWIMMERS
from .physics import BEACH_PROFILES, DEFAULT_PROFILE
from .shoreline import assumed_shoreline


def predict_payload(params: dict[str, list[str]]) -> dict:
    """Shared handler for /api/predict, kept out of the HTTP plumbing."""
    scenario_key = _one(params, "demo")
    profile = BEACH_PROFILES.get(_one(params, "beach") or "sandy", DEFAULT_PROFILE)
    swimmer = SWIMMERS.get(_one(params, "swimmer") or "average", SWIMMERS["average"])
    hours = _int(_one(params, "hours"), default=48, low=2, high=96)
    # A few hours of history so the scrubber can look back as well as forward,
    # and so the "now" marker sits inside the strip rather than at its edge.
    back = _int(_one(params, "back"), default=6, low=0, high=24)
    facing = _float(_one(params, "facing"))

    if scenario_key:
        scenario = demo_module.get(scenario_key)
        location = scenario.location
        shore = assumed_shoreline(facing if facing is not None else scenario.facing)
        forecast = sources.build(*demo_module.payloads(scenario))
    else:
        lat = _float(_one(params, "lat"))
        lon = _float(_one(params, "lon"))
        if lat is None or lon is None:
            raise ValueError("lat and lon are required")
        lat, lon = validate(lat, lon)
        label = _one(params, "label")
        location = Location(lat, lon, name=label, source="browser")
        shore = (
            assumed_shoreline(facing)
            if facing is not None
            else shoreline_module.detect(lat, lon)
        )
        forecast = sources.load(lat, lon)

    now = sources.now_local(forecast)
    hour = forecast.at(now)
    if hour is None:
        raise ValueError("no forecast data covers the current time at this location")

    shared = dict(profile=profile, swimmer=swimmer)
    prediction = model.predict(hour, shore, forecast=forecast, **shared)
    start = max(forecast.hours[0].time, now - timedelta(hours=back))
    timeline = model.predict_series(forecast, shore, start, hours + back, **shared)
    window = model.best_window(timeline, max_flag=Flag.YELLOW, forecast=forecast)

    payload = render.as_dict(
        prediction,
        location=location,
        shore=shore,
        forecast=forecast,
        profile=profile,
        timeline=timeline,
        window=window,
        now=now,
        detail=True,  # Every hour has to be renderable on its own for the scrubber.
    )
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return payload


def _one(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0].strip() if values and values[0].strip() else None


def _float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{raw!r} is not a number") from None


def _int(raw: str | None, *, default: int, low: int, high: int) -> int:
    if raw is None:
        return default
    try:
        return max(low, min(high, int(float(raw))))
    except ValueError:
        return default


FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" fill="none"/>'
    '<rect x="6" y="4" width="3" height="25" rx="1.4" fill="#5c6672"/>'
    '<path d="M9 5h17l-4.5 6L26 17H9z" fill="#D42222"/>'
    "</svg>"
)


class Handler(BaseHTTPRequestHandler):
    server_version = "beachflag"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if parsed.path == "/favicon.ico":
            return self._send(200, FAVICON.encode("utf-8"), "image/svg+xml")
        if parsed.path == "/api/demos":
            return self._json(200, {
                "scenarios": [
                    {"key": k, "title": s.title, "expectation": s.expectation}
                    for k, s in demo_module.SCENARIOS.items()
                ]
            })
        if parsed.path == "/api/geocode":
            return self._geocode(params)
        if parsed.path == "/api/predict":
            return self._predict(params)
        return self._json(404, {"error": "not found"})

    def _predict(self, params: dict[str, list[str]]) -> None:
        try:
            self._json(200, predict_payload(params))
        except FetchError as exc:
            self._json(502, {"error": exc.reason})
        except (ValueError, KeyError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - last-resort guard
            self._json(500, {"error": f"unexpected failure: {exc}"})

    def _geocode(self, params: dict[str, list[str]]) -> None:
        query = _one(params, "q")
        if not query:
            return self._json(400, {"error": "q is required"})
        try:
            found = geocode(query, count=5)
        except FetchError as exc:
            return self._json(404, {"error": exc.reason})
        self._json(200, {
            "results": [
                {
                    "label": place.label(),
                    "latitude": place.latitude,
                    "longitude": place.longitude,
                }
                for place in found
            ]
        })

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Geolocation needs a secure context; localhost counts as one.
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass  # The page is the interface; request logs are just noise.


def serve(port: int = 8765, *, host: str = "127.0.0.1", open_browser: bool = True) -> int:
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"Beach flag predictor running at {url}")
    print("Your browser will ask for location permission - that is how it finds your beach.")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beach flag predictor</title>
<style>
  :root {
    --bg:#f4f6f8; --card:#fff; --ink:#14181d; --muted:#5c6672;
    --line:#e2e6ea; --accent:#1f6feb; --night:rgba(20,24,29,.07);
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0e1116; --card:#161b22; --ink:#e6edf3; --muted:#8b949e;
            --line:#242c36; --accent:#4c8dff; --night:rgba(255,255,255,.05); }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  .wrap { max-width:780px; margin:0 auto; padding:24px 16px 64px; }
  h1 { font-size:20px; margin:0 0 4px; letter-spacing:-.01em; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:18px; margin-bottom:16px; }
  h3 { font-size:12px; text-transform:uppercase; letter-spacing:.08em;
       color:var(--muted); margin:0 0 12px; }

  /* flag banner */
  .flag { border-radius:14px; padding:22px; color:#fff; margin-bottom:16px;
          transition:background .18s ease; }
  .flag-top { display:flex; align-items:baseline; justify-content:space-between;
              gap:12px; flex-wrap:wrap; }
  .flag h2 { margin:0; font-size:30px; letter-spacing:.02em; }
  .flag .when { font-size:14px; font-weight:600; opacity:.95; }
  .flag .meaning { font-size:15px; opacity:.95; margin-top:2px; }
  .flag .advice { font-size:14px; opacity:.92; margin-top:12px;
                  border-top:1px solid rgba(255,255,255,.28); padding-top:12px; }

  /* scrubber */
  .strip { display:flex; gap:2px; align-items:flex-end; height:74px;
           touch-action:none; cursor:ew-resize; user-select:none; }
  .strip .bar { flex:1; border-radius:3px 3px 0 0; min-height:8px; position:relative;
                transition:opacity .12s ease; }
  .strip .bar.night::after { content:""; position:absolute; inset:0;
                             background:var(--night); border-radius:3px 3px 0 0; }
  .strip .bar.dayline { box-shadow:-1px 0 0 0 var(--muted); }
  .strip .bar.sel { outline:2px solid var(--ink); outline-offset:1px; z-index:2; }
  .strip .bar.now { box-shadow:inset 0 0 0 2px rgba(255,255,255,.85); }
  .strip .bar:not(.sel) { opacity:.72; }
  .ticks { display:flex; gap:2px; color:var(--muted); font-size:10px; margin-top:5px; }
  .ticks span { flex:1; text-align:center; white-space:nowrap; }
  input[type=range] { width:100%; margin:12px 0 4px; accent-color:var(--accent); }
  .scrub-foot { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }

  /* drivers + metrics */
  .driver { margin-bottom:14px; }
  .driver-head { display:flex; align-items:center; gap:10px; font-weight:600; font-size:14px; }
  .dot { width:10px; height:10px; border-radius:50%; flex:none; }
  .bar-track { flex:1; height:6px; border-radius:3px; background:var(--line); overflow:hidden; }
  .bar-track span { display:block; height:100%; border-radius:3px; transition:width .18s ease; }
  .driver-detail { color:var(--muted); font-size:13.5px; margin-top:5px; padding-left:20px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px 18px; }
  .metric .k { color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.05em; }
  .metric .v { font-size:15px; font-weight:600; margin-top:1px; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; }
  .chip { border-radius:999px; padding:5px 12px; font-size:12.5px; color:#fff; font-weight:600; }

  button { font:inherit; border:1px solid var(--line); background:var(--card); color:var(--ink);
           border-radius:9px; padding:9px 14px; cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
  button:hover { border-color:var(--accent); }
  input, select { font:inherit; padding:9px 11px; border-radius:9px;
                  border:1px solid var(--line); background:var(--bg); color:var(--ink); }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .row input[type=search] { flex:1; min-width:180px; }
  .place { font-weight:600; }
  .meta { color:var(--muted); font-size:12.5px; margin-top:4px; }
  .note { color:var(--muted); font-size:12.5px; }
  .warn { border-left:3px solid #d42222; padding-left:12px; }
  .hidden { display:none; }
  ul.results { list-style:none; margin:10px 0 0; padding:0; }
  ul.results li button { width:100%; text-align:left; margin-bottom:6px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Beach flag predictor</h1>
  <div class="sub">Which safety flag the beach is likely flying — now, or any hour you drag to.</div>

  <div class="card">
    <div class="row">
      <button class="primary" id="locate">Use my location</button>
      <input type="search" id="query" placeholder="or search a beach or town">
      <button id="search">Search</button>
    </div>
    <div class="row" style="margin-top:12px">
      <label class="note">Beach type
        <select id="beach">
          <option value="sandy" selected>Sandy, bars and rip channels</option>
          <option value="dissipative">Wide flat sand</option>
          <option value="steep">Steep sand or shingle</option>
          <option value="reef">Reef or rock platform</option>
          <option value="sheltered">Sheltered bay</option>
        </select>
      </label>
      <label class="note">Swimmer
        <select id="swimmer">
          <option value="strong">Strong</option>
          <option value="average" selected>Average</option>
          <option value="weak">Weak</option>
          <option value="child">Child</option>
          <option value="nonswimmer">Non-swimmer</option>
        </select>
      </label>
      <select id="demo" class="note"><option value="">Live data</option></select>
    </div>
    <ul class="results hidden" id="results"></ul>
    <div class="note" id="status" style="margin-top:10px"></div>
  </div>

  <div id="out"></div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

const state = { data: null, index: 0, url: null };

function status(text, warn) {
  $('status').textContent = text || '';
  $('status').className = warn ? 'note warn' : 'note';
}

function options() {
  return `&beach=${$('beach').value}&swimmer=${$('swimmer').value}`;
}

// Forecast times are local wall clock at the beach, with no zone suffix. Parsing
// them as UTC and formatting in UTC keeps them the beach's clock, not the
// viewer's - which is the whole point when you are checking a beach abroad.
const when = (iso) => new Date(iso + 'Z');
const hh = (iso) => String(when(iso).getUTCHours()).padStart(2, '0');
function stamp(iso) {
  const d = when(iso);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}, ${hh(iso)}:00`;
}

function offsetLabel(index) {
  const d = state.data;
  if (d.now_index == null) return '';
  const delta = index - d.now_index;
  if (delta === 0) return 'now';
  if (delta === 1) return 'in 1 hour';
  if (delta === -1) return '1 hour ago';
  return delta > 0 ? `in ${delta} hours` : `${-delta} hours ago`;
}

async function load(url) {
  status('Reading the ocean…');
  $('out').innerHTML = '';
  try {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    state.data = data;
    state.url = url;
    state.index = data.now_index == null ? 0 : data.now_index;
    status('');
    shell();
    paint();
  } catch (err) {
    status(err.message, true);
  }
}

function locate() {
  if (!navigator.geolocation) return status('This browser has no geolocation.', true);
  status('Asking your browser where you are…');
  navigator.geolocation.getCurrentPosition(
    (pos) => load(`/api/predict?lat=${pos.coords.latitude}&lon=${pos.coords.longitude}${options()}`),
    (err) => status('Location denied or unavailable: ' + err.message + '. Search for the beach instead.', true),
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
  );
}

async function search() {
  const q = $('query').value.trim();
  if (!q) return;
  status('Searching…');
  const response = await fetch('/api/geocode?q=' + encodeURIComponent(q));
  const data = await response.json();
  if (!response.ok) return status(data.error, true);
  status('');
  const list = $('results');
  list.innerHTML = data.results.map((r, i) =>
    `<li><button data-i="${i}">${esc(r.label)}</button></li>`).join('');
  list.classList.remove('hidden');
  list.querySelectorAll('button').forEach((button) => {
    button.onclick = () => {
      const place = data.results[+button.dataset.i];
      list.classList.add('hidden');
      load(`/api/predict?lat=${place.latitude}&lon=${place.longitude}` +
           `&label=${encodeURIComponent(place.label)}${options()}`);
    };
  });
}

/* ---------- layout, built once per fetch ---------- */

function shell() {
  const d = state.data;
  const bars = d.timeline.map((t, i) => {
    const night = t.observations && t.observations.is_day === false ? ' night' : '';
    const dayline = hh(t.time) === '00' && i > 0 ? ' dayline' : '';
    const nowMark = i === d.now_index ? ' now' : '';
    return `<div class="bar${night}${dayline}${nowMark}" data-i="${i}"
              style="background:${t.flag.color};height:${12 + t.hazard_index * 0.55}px"
              title="${stamp(t.time)} — ${esc(t.flag.label)}"></div>`;
  }).join('');

  const ticks = d.timeline.map((t, i) => {
    if (hh(t.time) === '00') return `<span>${DAYS[when(t.time).getUTCDay()]}</span>`;
    return `<span>${i % 3 === 0 ? hh(t.time) : ''}</span>`;
  }).join('');

  const windowNote = d.best_window
    ? `<span class="note">Calmest stretch: ${stamp(d.best_window.start)} to ${hh(d.best_window.end)}:00.</span>`
    : '';

  $('out').innerHTML = `
    <div class="flag" id="banner"></div>
    <div class="card">
      <h3>Timeline — drag to any hour</h3>
      <div class="strip" id="strip">${bars}</div>
      <div class="ticks">${ticks}</div>
      <input type="range" id="slider" min="0" max="${d.timeline.length - 1}" step="1">
      <div class="scrub-foot">
        <button id="nowBtn">Back to now</button>
        <span class="note">Drag the bars, the slider, or use the arrow keys.</span>
        ${windowNote}
      </div>
    </div>
    <div class="card">
      <div class="place">${esc(d.location.label)}</div>
      <div class="meta" id="meta"></div>
      <div style="margin-top:10px" id="headline"></div>
      <div class="chips" style="margin-top:14px" id="chips"></div>
    </div>
    <div class="card"><h3>Why</h3><div id="why"></div></div>
    <div class="card"><h3>Conditions</h3><div class="grid" id="metrics"></div></div>
    <div class="card note warn">${esc(d.disclaimer)}</div>`;

  const strip = $('strip');
  const pick = (event) => {
    const box = strip.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / box.width;
    select(Math.floor(ratio * d.timeline.length));
  };
  strip.addEventListener('pointerdown', (event) => {
    strip.setPointerCapture(event.pointerId);
    pick(event);
  });
  strip.addEventListener('pointermove', (event) => {
    if (event.buttons) pick(event);
  });
  $('slider').addEventListener('input', (event) => select(+event.target.value));
  $('nowBtn').onclick = () => select(d.now_index == null ? 0 : d.now_index);
}

function select(index) {
  const count = state.data.timeline.length;
  const next = Math.max(0, Math.min(count - 1, index));
  if (next === state.index) return;
  state.index = next;
  paint();
}

/* ---------- everything that changes as you scrub ---------- */

function paint() {
  const d = state.data;
  const f = d.timeline[state.index];
  if (!f) return;

  $('slider').value = state.index;
  document.querySelectorAll('.strip .bar').forEach((bar, i) =>
    bar.classList.toggle('sel', i === state.index));

  const label = offsetLabel(state.index);
  $('banner').style.background = f.flag.color;
  $('banner').innerHTML = `
    <div class="flag-top">
      <h2>${esc(f.flag.label)}</h2>
      <div class="when">${esc(stamp(f.time))}${label ? ' · ' + esc(label) : ''}</div>
    </div>
    <div class="meaning">${esc(f.flag.meaning)}</div>
    <div class="advice">${esc(f.flag.advice)}</div>`;

  $('meta').textContent =
    `${d.timezone} · beach faces ${d.shoreline.facing_compass} ` +
    `(${d.shoreline.facing_degrees.toFixed(0)}°) · ${d.beach_profile} · ` +
    `confidence ${(f.confidence * 100).toFixed(0)}% · hazard index ${f.hazard_index.toFixed(0)}/100`;
  $('headline').textContent = f.headline;

  $('chips').innerHTML = f.advisories.map(a =>
    `<span class="chip" style="background:${a.color}" title="${esc(a.meaning)}">${esc(a.label)}</span>`
  ).join('');

  $('why').innerHTML = f.drivers
    .filter(x => x.score >= 12 || x.demands !== 'green')
    .sort((a, b) => b.score - a.score).slice(0, 6).map(x => `
      <div class="driver">
        <div class="driver-head">
          <span class="dot" style="background:${levelColor(x.demands)}"></span>
          <span>${esc(x.label)}</span>
          <span class="bar-track"><span style="width:${x.score}%;background:${levelColor(x.demands)}"></span></span>
        </div>
        <div class="driver-detail">${esc(x.detail)}</div>
      </div>`).join('') || '<div class="note">Nothing scored above background.</div>';

  const m = f.metrics, o = f.observations || {};
  const rows = [
    ['Breaking surf', m.breaker_height_m > 0 ? `${m.breaker_height_m.toFixed(1)} m ${m.breaker_type}` : 'flat'],
    ['Swell', m.swell_height_m > 0 ? `${m.swell_height_m.toFixed(1)} m at ${m.swell_period_s.toFixed(0)} s` : null],
    ['Wave power', m.wave_power_kw_per_m > 0
      ? `${m.wave_power_kw_per_m.toFixed(m.wave_power_kw_per_m < 10 ? 1 : 0)} kW/m` : null],
    ['Rip current', m.rip_peak_ms > 0.05
      ? `${m.rip_speed_ms.toFixed(1)} m/s, ${m.rip_peak_ms.toFixed(1)} in pulses` : 'negligible'],
    ['Longshore drift', m.longshore_current_ms > 0.05 ? `${m.longshore_current_ms.toFixed(1)} m/s` : null],
    ['Surf zone width', m.surf_zone_width_m > 0 ? `${m.surf_zone_width_m.toFixed(0)} m` : null],
    ['Wind', o.wind_speed_ms != null
      ? `${o.wind_speed_ms.toFixed(0)} m/s ${m.onshore_wind_ms > 1 ? 'onshore'
        : (m.onshore_wind_ms < -1 ? 'offshore' : 'cross-shore')}` : null],
    ['Water', o.sea_temperature_c != null ? `${o.sea_temperature_c.toFixed(0)} °C` : null],
    ['Air', o.air_temperature_c != null ? `${o.air_temperature_c.toFixed(0)} °C` : null],
    ['Tide', o.tide_phase != null
      ? `${tideWord(o.tide_phase)}, ${o.tide_rising ? 'rising' : 'falling'}` : null],
    ['Beach state', m.beach_state],
    // UV is zero all night; showing it then is noise, not information.
    ['UV index', o.uv_index ? o.uv_index.toFixed(0) : null],
  ].filter(([, v]) => v != null);

  $('metrics').innerHTML = rows.map(([k, v]) =>
    `<div class="metric"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
}

function levelColor(level) {
  return { green:'#12A150', yellow:'#E6A700', red:'#D42222', double_red:'#8B0000' }[level] || '#5C6672';
}
function tideWord(p) {
  return p < 0.2 ? 'near low' : p < 0.45 ? 'low-mid' : p < 0.55 ? 'mid' : p < 0.8 ? 'mid-high' : 'near high';
}

$('locate').onclick = locate;
$('search').onclick = search;
$('query').onkeydown = (e) => { if (e.key === 'Enter') search(); };
$('beach').onchange = $('swimmer').onchange = () => {
  if (state.url) load(state.url.replace(/&beach=.*/, '') + options());
};
$('demo').onchange = () => { if ($('demo').value) load(`/api/predict?demo=${$('demo').value}${options()}`); };
document.addEventListener('keydown', (e) => {
  if (!state.data || /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  if (e.key === 'ArrowRight') { select(state.index + 1); e.preventDefault(); }
  if (e.key === 'ArrowLeft') { select(state.index - 1); e.preventDefault(); }
});

fetch('/api/demos').then(r => r.json()).then(d => {
  $('demo').innerHTML = '<option value="">Live data</option>' +
    d.scenarios.map(s => `<option value="${s.key}">Sample: ${esc(s.title)}</option>`).join('');
  // ?demo=storm or ?lat=..&lon=.. opens straight into a prediction.
  const q = new URLSearchParams(location.search);
  if (q.get('demo')) { $('demo').value = q.get('demo'); load(`/api/predict?demo=${q.get('demo')}${options()}`); }
  else if (q.get('lat') && q.get('lon')) {
    load(`/api/predict?lat=${q.get('lat')}&lon=${q.get('lon')}${options()}`);
  }
});
</script>
</body>
</html>
"""
