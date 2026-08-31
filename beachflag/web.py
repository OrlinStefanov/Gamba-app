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
from datetime import datetime
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
    hours = _int(_one(params, "hours"), default=24, low=0, high=96)
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
    timeline = model.predict_series(forecast, shore, now, hours, **shared)
    window = model.best_window(timeline, max_flag=Flag.YELLOW, forecast=forecast)

    payload = render.as_dict(
        prediction,
        location=location,
        shore=shore,
        forecast=forecast,
        profile=profile,
        timeline=timeline,
        window=window,
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


class Handler(BaseHTTPRequestHandler):
    server_version = "beachflag"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
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
    --bg: #f4f6f8; --card: #ffffff; --ink: #14181d; --muted: #5c6672;
    --line: #e2e6ea; --accent: #1f6feb;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0e1116; --card:#161b22; --ink:#e6edf3; --muted:#8b949e;
            --line:#242c36; --accent:#4c8dff; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  .wrap { max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 20px; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:18px; margin-bottom:16px; }
  .flag { border-radius:14px; padding:22px; color:#fff; margin-bottom:16px; }
  .flag h2 { margin:0; font-size:30px; letter-spacing:0.02em; }
  .flag .meaning { font-size:15px; opacity:.95; margin-top:2px; }
  .flag .advice { font-size:14px; opacity:.9; margin-top:12px;
                  border-top:1px solid rgba(255,255,255,.28); padding-top:12px; }
  .place { font-weight:600; }
  .meta { color:var(--muted); font-size:12.5px; margin-top:4px; }
  h3 { font-size:12px; text-transform:uppercase; letter-spacing:.08em;
       color:var(--muted); margin:0 0 12px; }
  .driver { margin-bottom:14px; }
  .driver-head { display:flex; align-items:center; gap:10px; font-weight:600; font-size:14px; }
  .dot { width:10px; height:10px; border-radius:50%; flex:none; }
  .bar { flex:1; height:6px; border-radius:3px; background:var(--line); overflow:hidden; }
  .bar span { display:block; height:100%; border-radius:3px; }
  .driver-detail { color:var(--muted); font-size:13.5px; margin-top:5px; padding-left:20px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px 18px; }
  .metric .k { color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.05em; }
  .metric .v { font-size:15px; font-weight:600; margin-top:1px; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; }
  .chip { border-radius:999px; padding:5px 12px; font-size:12.5px; color:#fff; font-weight:600; }
  .strip { display:flex; gap:2px; align-items:flex-end; height:56px; margin-bottom:6px; }
  .strip div { flex:1; border-radius:3px 3px 0 0; min-height:8px; }
  .ticks { display:flex; gap:2px; color:var(--muted); font-size:10px; }
  .ticks span { flex:1; text-align:center; }
  button { font:inherit; border:1px solid var(--line); background:var(--card); color:var(--ink);
           border-radius:9px; padding:9px 14px; cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
  button:hover { border-color:var(--accent); }
  input, select { font:inherit; padding:9px 11px; border-radius:9px;
                  border:1px solid var(--line); background:var(--bg); color:var(--ink); }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .row input[type=search] { flex:1; min-width:180px; }
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
  <div class="sub">What flag the beach is likely flying right now, from live marine and weather models.</div>

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
let last = null;

function status(text, warn) {
  $('status').textContent = text || '';
  $('status').className = warn ? 'note warn' : 'note';
}

function options() {
  return `&beach=${$('beach').value}&swimmer=${$('swimmer').value}`;
}

async function load(url) {
  status('Reading the ocean...');
  $('out').innerHTML = '';
  try {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    last = url;
    status('');
    draw(data);
  } catch (err) {
    status(err.message, true);
  }
}

function locate() {
  if (!navigator.geolocation) return status('This browser has no geolocation.', true);
  status('Asking your browser where you are...');
  navigator.geolocation.getCurrentPosition(
    (pos) => load(`/api/predict?lat=${pos.coords.latitude}&lon=${pos.coords.longitude}${options()}`),
    (err) => status('Location denied or unavailable: ' + err.message + '. Search for the beach instead.', true),
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
  );
}

async function search() {
  const q = $('query').value.trim();
  if (!q) return;
  status('Searching...');
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

function draw(d) {
  const f = d.flag;
  const advisories = d.advisories.map(a =>
    `<span class="chip" style="background:${a.color}" title="${esc(a.meaning)}">${esc(a.label)}</span>`).join('');
  const drivers = d.drivers.filter(x => x.score >= 12 || x.demands !== 'green')
    .sort((a, b) => b.score - a.score).slice(0, 6).map(x => `
      <div class="driver">
        <div class="driver-head">
          <span class="dot" style="background:${levelColor(x.demands)}"></span>
          <span>${esc(x.label)}</span>
          <span class="bar"><span style="width:${x.score}%;background:${levelColor(x.demands)}"></span></span>
        </div>
        <div class="driver-detail">${esc(x.detail)}</div>
      </div>`).join('') || '<div class="note">Nothing scored above background.</div>';

  const m = d.metrics, o = d.observations || {};
  const metrics = [
    ['Breaking surf', m.breaker_height_m > 0 ? `${m.breaker_height_m.toFixed(1)} m ${m.breaker_type}` : 'flat'],
    ['Swell', m.swell_height_m > 0 ? `${m.swell_height_m.toFixed(1)} m at ${m.swell_period_s.toFixed(0)} s` : '-'],
    ['Wave power', `${m.wave_power_kw_per_m.toFixed(0)} kW/m`],
    ['Rip current', m.rip_peak_ms > 0.05 ? `${m.rip_speed_ms.toFixed(1)} m/s, ${m.rip_peak_ms.toFixed(1)} in pulses` : 'negligible'],
    ['Longshore drift', m.longshore_current_ms > 0.05 ? `${m.longshore_current_ms.toFixed(1)} m/s` : 'negligible'],
    ['Surf zone width', `${m.surf_zone_width_m.toFixed(0)} m`],
    ['Wind', o.wind_speed_ms != null ? `${o.wind_speed_ms.toFixed(0)} m/s ${m.onshore_wind_ms > 1 ? 'onshore' : (m.onshore_wind_ms < -1 ? 'offshore' : 'cross-shore')}` : '-'],
    ['Water', o.sea_temperature_c != null ? `${o.sea_temperature_c.toFixed(0)} C` : '-'],
    ['Tide', o.tide_phase != null ? `${tideWord(o.tide_phase)}, ${o.tide_rising ? 'rising' : 'falling'}` : '-'],
    ['Beach faces', `${d.shoreline.facing_compass} (${d.shoreline.facing_degrees.toFixed(0)} deg)`],
    ['Beach state', m.beach_state],
    ['UV index', o.uv_index != null ? o.uv_index.toFixed(0) : '-'],
  ].filter(([, v]) => v !== '-').map(([k, v]) =>
    `<div class="metric"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');

  const strip = d.timeline.map(t =>
    `<div style="background:${t.color};height:${18 + t.level * 12}px" title="${t.time.slice(11, 16)} ${t.flag}"></div>`).join('');
  const ticks = d.timeline.map((t, i) =>
    `<span>${i % 3 === 0 ? t.time.slice(11, 13) : ''}</span>`).join('');

  const window = d.best_window
    ? `<div class="note" style="margin-top:10px">Calmest stretch ahead: ${d.best_window.start.slice(11, 16)} to ${d.best_window.end.slice(11, 16)}.</div>`
    : '';

  $('out').innerHTML = `
    <div class="flag" style="background:${f.color}">
      <h2>${esc(f.label)}</h2>
      <div class="meaning">${esc(f.meaning)}</div>
      <div class="advice">${esc(f.advice)}</div>
    </div>
    <div class="card">
      <div class="place">${esc(d.location.label)}</div>
      <div class="meta">${esc(d.time.replace('T', ' ').slice(0, 16))} ${esc(d.timezone)} ·
        ${esc(d.beach_profile)} · confidence ${(d.confidence * 100).toFixed(0)}% ·
        hazard index ${d.hazard_index.toFixed(0)}/100</div>
      <div style="margin-top:10px">${esc(d.headline)}</div>
      ${advisories ? `<div class="chips" style="margin-top:14px">${advisories}</div>` : ''}
    </div>
    <div class="card"><h3>Why</h3>${drivers}</div>
    <div class="card"><h3>Conditions</h3><div class="grid">${metrics}</div></div>
    <div class="card"><h3>Next hours</h3><div class="strip">${strip}</div><div class="ticks">${ticks}</div>${window}</div>
    <div class="card note warn">${esc(d.disclaimer)}</div>`;
}

function levelColor(level) {
  return { green: '#12A150', yellow: '#E6A700', red: '#D42222', double_red: '#8B0000' }[level] || '#5C6672';
}
function tideWord(p) {
  return p < 0.2 ? 'near low' : p < 0.45 ? 'low-mid' : p < 0.55 ? 'mid' : p < 0.8 ? 'mid-high' : 'near high';
}

$('locate').onclick = locate;
$('search').onclick = search;
$('query').onkeydown = (e) => { if (e.key === 'Enter') search(); };
$('beach').onchange = $('swimmer').onchange = () => { if (last) load(last.replace(/&beach=.*/, '') + options()); };
$('demo').onchange = () => { if ($('demo').value) load(`/api/predict?demo=${$('demo').value}${options()}`); };

fetch('/api/demos').then(r => r.json()).then(d => {
  $('demo').innerHTML = '<option value="">Live data</option>' +
    d.scenarios.map(s => `<option value="${s.key}">Sample: ${esc(s.title)}</option>`).join('');
});
</script>
</body>
</html>
"""
