// The page. Everything is computed in the browser: there is no server behind
// this, only Open-Meteo for the raw forecast.

import { ApiError, ShorelineError, detectShoreline, geocode, loadForecast } from './api.js';
import { hourAt, nowLocal } from './sources.js';
import { ADVISORY_INFO, FLAG_INFO, SWIMMERS } from './flags.js';
import { BEACH_PROFILES } from './physics.js';
import { bestWindow, predictSeries, topDrivers } from './model.js';
import { compassPoint } from './geo.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const BACK_HOURS = 6;
const AHEAD_HOURS = 48;

const state = { frames: [], index: 0, nowIndex: 0, context: null, place: null };

// Forecast stamps are local wall clock at the beach with no zone suffix. Parse
// as UTC and read back in UTC so they stay the beach's clock, not the phone's.
const when = (stamp) => new Date(stamp + 'Z');
const hh = (stamp) => String(when(stamp).getUTCHours()).padStart(2, '0');
const stampLabel = (s) => {
  const d = when(s);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}, ${hh(s)}:00`;
};

function status(text, kind) {
  const node = $('status');
  node.className = kind === 'error' ? 'note warn' : 'note';
  node.innerHTML = kind === 'busy' ? `<span class="spinner"></span>${esc(text)}` : esc(text);
}

// --- tiny persistence, so reopening the app does not start from nothing ------

function remember(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}
function recall(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch { return fallback; }
}

// --- running a prediction ----------------------------------------------------

async function run(latitude, longitude, label) {
  $('results').classList.add('hidden');
  $('out').innerHTML = '';
  state.place = { latitude, longitude, label };
  remember('beachflag.place', state.place);

  try {
    status('Working out which way the beach faces…', 'busy');
    const shore = await detectShoreline(latitude, longitude);

    status('Reading the ocean…', 'busy');
    const forecast = await loadForecast(latitude, longitude);

    const now = nowLocal(forecast);
    if (!hourAt(forecast, now)) throw new ApiError('no forecast covers the current time here', '');

    const options = {
      profile: BEACH_PROFILES[$('beach').value] || BEACH_PROFILES.sandy,
      swimmer: SWIMMERS[$('swimmer').value] || SWIMMERS.average,
    };

    const startMs = when(now).getTime() - BACK_HOURS * 3600000;
    const start = new Date(startMs).toISOString().slice(0, 16);
    const frames = predictSeries(forecast, shore, start, BACK_HOURS + AHEAD_HOURS, options);
    if (!frames.length) throw new ApiError('the forecast came back empty', '');

    let nowIndex = 0;
    let gap = Infinity;
    frames.forEach((f, i) => {
      const d = Math.abs(when(f.time).getTime() - when(now).getTime());
      if (d < gap) { gap = d; nowIndex = i; }
    });

    state.frames = frames;
    state.nowIndex = nowIndex;
    state.index = nowIndex;
    state.context = { shore, forecast, label, options };

    status(forecast.missing.length ? `Note: ${forecast.missing.join('; ')}.` : '');
    shell();
    paint();
  } catch (err) {
    if (err instanceof ShorelineError) {
      status(`${err.message}. Search for a beach instead.`, 'error');
    } else if (err instanceof ApiError) {
      status(`${err.host ? err.host + ': ' : ''}${err.message}`, 'error');
    } else {
      status(`Something went wrong: ${err.message}`, 'error');
    }
  }
}

function locate() {
  if (!navigator.geolocation) return status('This browser has no geolocation.', 'error');
  status('Asking your phone where you are…', 'busy');
  navigator.geolocation.getCurrentPosition(
    (pos) => run(pos.coords.latitude, pos.coords.longitude, null),
    (err) => status(`Location unavailable: ${err.message}. Search for the beach instead.`, 'error'),
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 }
  );
}

async function search() {
  const q = $('query').value.trim();
  if (!q) return;
  status('Searching…', 'busy');
  try {
    const results = await geocode(q);
    status('');
    const list = $('results');
    list.innerHTML = results
      .map((r, i) => `<li><button data-i="${i}">${esc(r.label)}</button></li>`).join('');
    list.classList.remove('hidden');
    list.querySelectorAll('button').forEach((button) => {
      button.onclick = () => {
        const place = results[Number(button.dataset.i)];
        run(place.latitude, place.longitude, place.label);
      };
    });
  } catch (err) {
    status(err.message, 'error');
  }
}

// --- layout, built once per prediction ---------------------------------------

function shell() {
  const frames = state.frames;
  const bars = frames.map((f, i) => {
    const night = f.isDay === false ? ' night' : '';
    const dayline = hh(f.time) === '00' && i > 0 ? ' dayline' : '';
    const nowMark = i === state.nowIndex ? ' now' : '';
    return `<div class="bar${night}${dayline}${nowMark}"
      style="background:${FLAG_INFO[f.flag].color};height:${12 + f.score * 0.55}px"
      title="${stampLabel(f.time)} — ${FLAG_INFO[f.flag].label}"></div>`;
  }).join('');

  const ticks = frames.map((f, i) => (hh(f.time) === '00'
    ? `<span>${DAYS[when(f.time).getUTCDay()]}</span>`
    : `<span>${i % 3 === 0 ? hh(f.time) : ''}</span>`)).join('');

  const window = bestWindow(frames);
  const windowNote = window
    ? `<span class="note">Calmest stretch: ${stampLabel(window[0])} to ${hh(window[1])}:00.</span>`
    : '';

  // Geolocation gives coordinates and no name, and Open-Meteo has no reverse
  // lookup, so say something human and keep the numbers in the detail line.
  const { label } = state.context;
  const place = label || 'Your location';

  $('out').innerHTML = `
    <div class="flag" id="banner"></div>
    <div class="card">
      <h3>Timeline — drag to any hour</h3>
      <div class="strip" id="strip">${bars}</div>
      <div class="ticks">${ticks}</div>
      <input type="range" id="slider" min="0" max="${frames.length - 1}" step="1"
             aria-label="Hour of the forecast">
      <div class="scrub-foot">
        <button id="nowBtn">Back to now</button>
        <span class="note">Drag the bars or the slider.</span>
        ${windowNote}
      </div>
    </div>
    <div class="card">
      <div class="place">${esc(place)}</div>
      <div class="meta" id="meta"></div>
      <div style="margin-top:10px" id="headline"></div>
      <div class="chips" style="margin-top:14px" id="chips"></div>
    </div>
    <div class="card"><h3>Why</h3><div id="why"></div></div>
    <div class="card"><h3>Conditions</h3><div class="grid" id="metrics"></div></div>
    <div class="card note warn">Model estimate, not an official forecast and not a substitute for a
      lifeguard. It cannot see today's sandbars or a local closure. Where a real flag is flying,
      that flag wins.</div>`;

  const strip = $('strip');
  const pick = (event) => {
    const box = strip.getBoundingClientRect();
    select(Math.floor(((event.clientX - box.left) / box.width) * frames.length));
  };
  strip.addEventListener('pointerdown', (event) => {
    strip.setPointerCapture(event.pointerId);
    pick(event);
  });
  strip.addEventListener('pointermove', (event) => { if (event.buttons) pick(event); });
  $('slider').addEventListener('input', (event) => select(Number(event.target.value)));
  $('nowBtn').onclick = () => select(state.nowIndex);
}

function select(index) {
  const next = Math.max(0, Math.min(state.frames.length - 1, index));
  if (next === state.index) return;
  state.index = next;
  paint();
}

function offsetLabel(index) {
  const delta = index - state.nowIndex;
  if (delta === 0) return 'now';
  if (delta === 1) return 'in 1 hour';
  if (delta === -1) return '1 hour ago';
  return delta > 0 ? `in ${delta} hours` : `${-delta} hours ago`;
}

// --- everything that changes as you scrub ------------------------------------

function paint() {
  const f = state.frames[state.index];
  if (!f) return;
  const info = FLAG_INFO[f.flag];
  const { shore, forecast, options } = state.context;

  $('slider').value = state.index;
  document.querySelectorAll('.strip .bar').forEach((bar, i) =>
    bar.classList.toggle('sel', i === state.index));

  const themeColor = document.querySelector('meta[name=theme-color]');
  if (themeColor) themeColor.setAttribute('content', info.color);

  $('banner').style.background = info.color;
  $('banner').innerHTML = `
    <div class="flag-top">
      <h2>${esc(info.label)}</h2>
      <div class="when">${esc(stampLabel(f.time))} · ${esc(offsetLabel(state.index))}</div>
    </div>
    <div class="meaning">${esc(info.meaning)}</div>
    <div class="advice">${esc(info.advice)}</div>`;

  const where = state.context.label
    ? ''
    : `${state.place.latitude.toFixed(3)}, ${state.place.longitude.toFixed(3)} · `;
  $('meta').textContent = where
    + `${forecast.timezoneName} · beach faces ${compassPoint(shore.facing)} `
    + `(${shore.facing.toFixed(0)}°) · ${options.profile.name} · `
    + `confidence ${(f.confidence * 100).toFixed(0)}% · hazard index ${f.score.toFixed(0)}/100`;
  $('headline').textContent = f.headline;

  $('chips').innerHTML = f.advisories.map((key) => {
    const a = ADVISORY_INFO[key];
    return `<span class="chip" style="background:${a.color}" title="${esc(a.meaning)}">${esc(a.label)}</span>`;
  }).join('');

  $('why').innerHTML = topDrivers(f, 6).map((d) => `
    <div class="driver">
      <div class="driver-head">
        <span class="dot" style="background:${FLAG_INFO[d.demand].color}"></span>
        <span>${esc(d.label)}</span>
        <span class="bar-track"><span style="width:${d.score}%;background:${FLAG_INFO[d.demand].color}"></span></span>
      </div>
      <div class="driver-detail">${esc(d.detail)}</div>
    </div>`).join('') || '<div class="note">Nothing scored above background.</div>';

  const m = f.metrics;
  const o = hourAt(forecast, f.time) || {};
  const rows = [
    ['Breaking surf', m.breakerHeight > 0 ? `${m.breakerHeight.toFixed(1)} m ${m.breakerType}` : 'flat'],
    ['Swell', m.deepHeight > 0 ? `${m.deepHeight.toFixed(1)} m at ${m.deepPeriod.toFixed(0)} s` : null],
    ['Wave power', m.wavePower > 0 ? `${m.wavePower.toFixed(m.wavePower < 10 ? 1 : 0)} kW/m` : null],
    ['Rip current', m.ripPeak > 0.05
      ? `${m.ripSpeed.toFixed(1)} m/s, ${m.ripPeak.toFixed(1)} in pulses` : 'negligible'],
    ['Longshore drift', m.longshoreCurrent > 0.05 ? `${m.longshoreCurrent.toFixed(1)} m/s` : null],
    ['Surf zone width', m.surfZoneWidth > 0 ? `${m.surfZoneWidth.toFixed(0)} m` : null],
    ['Wind', o.windSpeed != null
      ? `${o.windSpeed.toFixed(0)} m/s ${m.onshoreWind > 1 ? 'onshore' : (m.onshoreWind < -1 ? 'offshore' : 'cross-shore')}`
      : null],
    ['Water', o.seaTemperature != null ? `${o.seaTemperature.toFixed(0)} °C` : null],
    ['Air', o.airTemperature != null ? `${o.airTemperature.toFixed(0)} °C` : null],
    ['Tide', o.tidePhase != null ? `${tideWord(o.tidePhase)}, ${o.tideRising ? 'rising' : 'falling'}` : null],
    ['Beach state', m.beachState],
    ['UV index', o.uvIndex ? o.uvIndex.toFixed(0) : null],
  ].filter(([, v]) => v != null);

  $('metrics').innerHTML = rows.map(([k, v]) =>
    `<div class="metric"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
}

function tideWord(p) {
  return p < 0.2 ? 'near low' : p < 0.45 ? 'low-mid' : p < 0.55 ? 'mid' : p < 0.8 ? 'mid-high' : 'near high';
}

// --- wiring ------------------------------------------------------------------

$('locate').onclick = locate;
$('search').onclick = search;
$('query').addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });

for (const id of ['beach', 'swimmer']) {
  $(id).value = recall(`beachflag.${id}`, $(id).value);
  $(id).addEventListener('change', () => {
    remember(`beachflag.${id}`, $(id).value);
    if (state.place) run(state.place.latitude, state.place.longitude, state.context?.label);
  });
}

document.addEventListener('keydown', (e) => {
  if (!state.frames.length || /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  if (e.key === 'ArrowRight') { select(state.index + 1); e.preventDefault(); }
  if (e.key === 'ArrowLeft') { select(state.index - 1); e.preventDefault(); }
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}

const params = new URLSearchParams(location.search);
if (params.get('lat') && params.get('lon')) {
  run(Number(params.get('lat')), Number(params.get('lon')), params.get('label'));
} else {
  const last = recall('beachflag.place', null);
  if (last) {
    status(`Showing your last beach. Tap "Use my location" for where you are now.`);
    run(last.latitude, last.longitude, last.label);
  } else {
    status('Tap "Use my location", or search for a beach.');
  }
}
