// Bearing arithmetic. Port of the angle helpers in beachflag/geo.py.
//
// JavaScript's % keeps the sign of the dividend where Python's does not, so
// every modulo here goes through a non-negative helper. Getting this wrong
// silently mirrors the beach.

const mod = (value, n) => ((value % n) + n) % n;

export function wrap360(degrees) {
  const value = mod(degrees, 360);
  return value >= 360 ? 0 : value;
}

export function wrap180(degrees) {
  const value = mod(degrees + 180, 360) - 180;
  return value === -180 ? 180 : value;
}

export function angleBetween(a, b) {
  return Math.abs(wrap180(a - b));
}

const POINTS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

export function compassPoint(degrees) {
  return POINTS[Math.floor(mod(wrap360(degrees) + 11.25, 360) / 22.5) % 16];
}

export function meanBearing(bearings, weights) {
  if (!bearings.length) return null;
  const w = weights || bearings.map(() => 1);
  let x = 0;
  let y = 0;
  bearings.forEach((b, i) => {
    x += w[i] * Math.cos((b * Math.PI) / 180);
    y += w[i] * Math.sin((b * Math.PI) / 180);
  });
  if (Math.abs(x) < 1e-12 && Math.abs(y) < 1e-12) return null;
  return wrap360((Math.atan2(y, x) * 180) / Math.PI);
}

export function bearingSpread(bearings) {
  if (!bearings.length) return 1;
  const n = bearings.length;
  let x = 0;
  let y = 0;
  bearings.forEach((b) => {
    x += Math.cos((b * Math.PI) / 180);
    y += Math.sin((b * Math.PI) / 180);
  });
  return Math.max(0, Math.min(1, 1 - Math.hypot(x / n, y / n)));
}

const EARTH_RADIUS_M = 6371000;

export function offset(latitude, longitude, bearing, distanceM) {
  const lat1 = (latitude * Math.PI) / 180;
  const lon1 = (longitude * Math.PI) / 180;
  const theta = (bearing * Math.PI) / 180;
  const delta = distanceM / EARTH_RADIUS_M;
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(delta) + Math.cos(lat1) * Math.sin(delta) * Math.cos(theta)
  );
  const lon2 = lon1 + Math.atan2(
    Math.sin(theta) * Math.sin(delta) * Math.cos(lat1),
    Math.cos(delta) - Math.sin(lat1) * Math.sin(lat2)
  );
  return [(lat2 * 180) / Math.PI, wrap180((lon2 * 180) / Math.PI)];
}
