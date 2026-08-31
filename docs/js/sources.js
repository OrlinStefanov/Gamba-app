// Parsing and normalising Open-Meteo payloads. Port of beachflag/sources.py.
//
// Times stay as the API's local wall-clock strings ("2026-08-31T14:00"). Turning
// them into Date objects would drag the viewer's own timezone into a forecast
// that is already local to the beach, so the model reads the hour off the
// string instead.

export const MARINE_URL = 'https://marine-api.open-meteo.com/v1/marine';
export const FORECAST_URL = 'https://api.open-meteo.com/v1/forecast';

export const MARINE_HOURLY = [
  'wave_height', 'wave_direction', 'wave_period',
  'wind_wave_height', 'wind_wave_direction', 'wind_wave_period',
  'swell_wave_height', 'swell_wave_direction', 'swell_wave_period',
  'sea_surface_temperature', 'ocean_current_velocity', 'ocean_current_direction',
  'sea_level_height_msl',
];

export const WEATHER_HOURLY = [
  'temperature_2m', 'apparent_temperature', 'relative_humidity_2m',
  'precipitation', 'precipitation_probability', 'weather_code', 'cloud_cover',
  'visibility', 'wind_speed_10m', 'wind_direction_10m', 'wind_gusts_10m',
  'uv_index', 'cape', 'is_day',
];

export const WEATHER_DAILY = ['sunrise', 'sunset', 'uv_index_max'];
export const THUNDERSTORM_CODES = new Set([95, 96, 99]);
export const PAST_DAYS = 2;

const num = (value) => {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  const n = Number(value);
  return Number.isNaN(n) ? null : n;
};
const int = (value) => { const n = num(value); return n === null ? null : Math.trunc(n); };
const bool = (value) => { const n = num(value); return n === null ? null : Boolean(n); };
const at = (hourly, key, i) => (Array.isArray(hourly[key]) && i < hourly[key].length ? hourly[key][i] : null);

export function isThunderstorm(hour) {
  return hour.weatherCode !== null && THUNDERSTORM_CODES.has(hour.weatherCode);
}

export function dominantSwell(hour) {
  if (hour.swellHeight && hour.swellPeriod && hour.swellHeight >= 0.25) {
    const windH = hour.windWaveHeight || 0;
    if (hour.swellHeight >= windH * 0.7) {
      return [hour.swellHeight, hour.swellPeriod, hour.swellDirection];
    }
  }
  if (hour.waveHeight && hour.wavePeriod) return [hour.waveHeight, hour.wavePeriod, hour.waveDirection];
  if (hour.windWaveHeight && hour.windWavePeriod) {
    return [hour.windWaveHeight, hour.windWavePeriod, hour.windWaveDirection];
  }
  return [null, null, null];
}

export class ForecastError extends Error {}

export function build(marine, weather, missing = []) {
  const weatherHourly = (weather && weather.hourly) || {};
  const marineHourly = (marine && marine.hourly) || {};
  const times = weatherHourly.time || [];
  if (!times.length) throw new ForecastError('the forecast contained no hourly timeline');

  const marineIndex = new Map();
  (marineHourly.time || []).forEach((stamp, i) => {
    const row = {};
    for (const key of Object.keys(marineHourly)) {
      if (key !== 'time') row[key] = at(marineHourly, key, i);
    }
    marineIndex.set(stamp, row);
  });

  const precipitation = times.map((_, i) => num(at(weatherHourly, 'precipitation', i)));

  const hours = times.map((stamp, i) => {
    const m = marineIndex.get(stamp) || {};
    const current = num(m.ocean_current_velocity);
    return {
      time: stamp,
      hourOfDay: Number(String(stamp).slice(11, 13)),
      waveHeight: num(m.wave_height),
      wavePeriod: num(m.wave_period),
      waveDirection: num(m.wave_direction),
      swellHeight: num(m.swell_wave_height),
      swellPeriod: num(m.swell_wave_period),
      swellDirection: num(m.swell_wave_direction),
      windWaveHeight: num(m.wind_wave_height),
      windWavePeriod: num(m.wind_wave_period),
      windWaveDirection: num(m.wind_wave_direction),
      seaTemperature: num(m.sea_surface_temperature),
      // ocean_current_velocity comes back in km/h; everything else is m/s.
      currentSpeed: current === null ? null : current / 3.6,
      currentDirection: num(m.ocean_current_direction),
      seaLevel: num(m.sea_level_height_msl),
      tidePhase: null,
      tideRising: null,
      tideRange: null,
      airTemperature: num(at(weatherHourly, 'temperature_2m', i)),
      apparentTemperature: num(at(weatherHourly, 'apparent_temperature', i)),
      humidity: num(at(weatherHourly, 'relative_humidity_2m', i)),
      windSpeed: num(at(weatherHourly, 'wind_speed_10m', i)),
      windGusts: num(at(weatherHourly, 'wind_gusts_10m', i)),
      windDirection: num(at(weatherHourly, 'wind_direction_10m', i)),
      precipitation: num(at(weatherHourly, 'precipitation', i)),
      precipitationProbability: num(at(weatherHourly, 'precipitation_probability', i)),
      weatherCode: int(at(weatherHourly, 'weather_code', i)),
      cloudCover: num(at(weatherHourly, 'cloud_cover', i)),
      visibility: num(at(weatherHourly, 'visibility', i)),
      uvIndex: num(at(weatherHourly, 'uv_index', i)),
      cape: num(at(weatherHourly, 'cape', i)),
      isDay: bool(at(weatherHourly, 'is_day', i)),
      rain24h: windowSum(precipitation, i, 24),
      rain48h: windowSum(precipitation, i, 48),
    };
  });

  addTideTerms(hours);

  const daily = (weather && weather.daily) || {};
  return {
    hours,
    sunrise: daily.sunrise || [],
    sunset: daily.sunset || [],
    utcOffsetSeconds: Number(weather.utc_offset_seconds || 0),
    timezoneName: weather.timezone || 'UTC',
    missing,
  };
}

function windowSum(series, index, hours) {
  const start = index - hours + 1;
  if (start < 0) return null;
  const values = series.slice(start, index + 1).filter((v) => v !== null);
  if (!values.length) return null;
  return Math.round(values.reduce((a, b) => a + b, 0) * 100) / 100;
}

function addTideTerms(hours) {
  const levels = hours.map((h) => h.seaLevel);
  if (!levels.some((v) => v !== null)) return;

  hours.forEach((hour, i) => {
    const level = levels[i];
    if (level === null) return;
    const window = levels.slice(Math.max(0, i - 6), Math.min(levels.length, i + 7))
      .filter((v) => v !== null);
    if (window.length < 3) return;

    const low = Math.min(...window);
    const high = Math.max(...window);
    const span = high - low;
    const phase = span < 0.05 ? 0.5 : (level - low) / span;

    let previous = null;
    for (let j = i - 1; j > Math.max(-1, i - 4); j -= 1) {
      if (levels[j] !== null) { previous = levels[j]; break; }
    }

    hour.tidePhase = Math.round(phase * 1000) / 1000;
    hour.tideRising = previous === null ? null : level > previous;
    hour.tideRange = Math.round(span * 1000) / 1000;
  });
}

/** The forecast hour covering a wall-clock stamp, or null outside the window. */
export function hourAt(forecast, stamp) {
  if (!forecast.hours.length) return null;
  const target = Date.parse(stamp + 'Z');
  let best = null;
  let bestGap = Infinity;
  for (const hour of forecast.hours) {
    const gap = Math.abs(Date.parse(hour.time + 'Z') - target);
    if (gap < bestGap) { bestGap = gap; best = hour; }
  }
  return bestGap > 5400000 ? null : best;
}

export function futureHours(forecast, startStamp, count) {
  const target = Date.parse(startStamp + 'Z') - 1800000;
  return forecast.hours.filter((h) => Date.parse(h.time + 'Z') >= target).slice(0, count);
}

/** Current wall-clock time at the beach, as the same stamp format the API uses. */
export function nowLocal(forecast) {
  const shifted = new Date(Date.now() + forecast.utcOffsetSeconds * 1000);
  return shifted.toISOString().slice(0, 13) + ':00';
}
