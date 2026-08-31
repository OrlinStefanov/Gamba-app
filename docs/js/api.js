// Open-Meteo access, straight from the browser.
//
// Open-Meteo sends Access-Control-Allow-Origin: *, so the page can call it
// directly and there is no server in the middle. Nothing here needs an API key,
// and your coordinates go only to Open-Meteo.

import { FORECAST_URL, MARINE_URL, MARINE_HOURLY, PAST_DAYS, WEATHER_DAILY, WEATHER_HOURLY, build }
  from './sources.js';
import { ELEVATION_URL, ShorelineError, fromElevations, probePoints } from './shoreline.js';

const GEOCODE_URL = 'https://geocoding-api.open-meteo.com/v1/search';

export class ApiError extends Error {
  constructor(message, host) {
    super(message);
    this.host = host;
  }
}

function url(base, params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue;
    search.set(key, Array.isArray(value) ? value.join(',') : String(value));
  }
  return `${base}?${search}`;
}

async function getJson(base, params, { timeout = 20000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const host = new URL(base).host;
  try {
    const response = await fetch(url(base, params), { signal: controller.signal });
    if (!response.ok) {
      let reason = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body && body.reason) reason += `: ${body.reason}`;
      } catch { /* a body we cannot read is not more informative than the status */ }
      throw new ApiError(reason, host);
    }
    return await response.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err.name === 'AbortError') throw new ApiError('timed out', host);
    throw new ApiError('could not be reached - check your connection', host);
  } finally {
    clearTimeout(timer);
  }
}

export async function geocode(query, count = 5) {
  const payload = await getJson(GEOCODE_URL, { name: query, count, format: 'json' });
  const results = payload.results || [];
  if (!results.length) throw new ApiError(`nothing matched "${query}"`, 'geocoding-api.open-meteo.com');
  return results.map((r) => ({
    latitude: r.latitude,
    longitude: r.longitude,
    label: [r.name, r.admin1 !== r.name ? r.admin1 : null, r.country].filter(Boolean).join(', '),
  }));
}

export async function detectShoreline(latitude, longitude) {
  const points = probePoints(latitude, longitude);
  const payload = await getJson(ELEVATION_URL, {
    latitude: points.map((p) => p.lat.toFixed(6)),
    longitude: points.map((p) => p.lon.toFixed(6)),
  });
  if (!Array.isArray(payload.elevation) || payload.elevation.length !== points.length) {
    throw new ApiError('the elevation service returned an unexpected shape', 'api.open-meteo.com');
  }
  return fromElevations(points, payload.elevation);
}

export async function loadForecast(latitude, longitude, forecastDays = 4) {
  const common = {
    latitude: latitude.toFixed(6),
    longitude: longitude.toFixed(6),
    timezone: 'auto',
    past_days: PAST_DAYS,
    forecast_days: Math.max(1, Math.min(7, forecastDays)),
  };
  const missing = [];

  // The two requests are independent, and the marine one is allowed to fail:
  // a lake or an estuary has no marine grid cell, and the atmospheric half
  // still says something useful.
  const [marineResult, weather] = await Promise.all([
    getJson(MARINE_URL, { ...common, hourly: MARINE_HOURLY }).catch((err) => {
      missing.push(`marine data (${err.message})`);
      return {};
    }),
    getJson(FORECAST_URL, {
      ...common,
      hourly: WEATHER_HOURLY,
      daily: WEATHER_DAILY,
      wind_speed_unit: 'ms',
    }),
  ]);

  return build(marineResult, weather, missing);
}

export { ShorelineError };
