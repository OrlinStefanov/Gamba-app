// Which way the beach faces, derived from terrain. Port of beachflag/shoreline.py.

import { angleBetween, bearingSpread, meanBearing, offset, wrap360 } from './geo.js';

export const ELEVATION_URL = 'https://api.open-meteo.com/v1/elevation';
export const PROBE_BEARINGS = Array.from({ length: 24 }, (_, i) => i * 15);
export const PROBE_RADII_M = [500, 1200, 2500, 5000];
const SEA_LEVEL_THRESHOLD_M = 0;

export function probePoints(latitude, longitude) {
  const points = [];
  for (const radius of PROBE_RADII_M) {
    for (const bearing of PROBE_BEARINGS) {
      const [lat, lon] = offset(latitude, longitude, bearing, radius);
      points.push({ lat, lon, bearing, radius });
    }
  }
  return points;
}

export function shelterFactor(shore, waveFrom) {
  if (angleBetween(waveFrom, shore.facing) >= 100) return 0;
  if (!shore.exposedBearings || !shore.exposedBearings.length) return 1;
  const nearestOpen = Math.min(...shore.exposedBearings.map((b) => angleBetween(waveFrom, b)));
  if (nearestOpen <= 15) return 1;
  if (nearestOpen >= 45) return 0.15;
  return 1 - 0.85 * ((nearestOpen - 15) / 30);
}

export function isSheltered(shore) {
  return shore.openWaterFraction < 0.28;
}

export function assumedShoreline(facing) {
  const f = wrap360(facing);
  const exposed = [];
  for (let d = -75; d < 80; d += 15) exposed.push(wrap360(f + d));
  return {
    facing: f,
    confidence: 1,
    openWaterFraction: 0.5,
    nearestWaterM: 0,
    exposedBearings: exposed,
    source: 'user',
  };
}

export class ShorelineError extends Error {}

export function fromElevations(points, elevations) {
  const waterBearings = [];
  const waterWeights = [];
  let nearestWater = null;
  const outerRadius = Math.max(...points.map((p) => p.radius));
  const exposed = [];
  let usable = 0;

  points.forEach((point, index) => {
    const elevation = elevations[index];
    if (elevation === null || elevation === undefined) return;
    usable += 1;
    if (elevation > SEA_LEVEL_THRESHOLD_M) return;
    waterBearings.push(point.bearing);
    waterWeights.push(1 / Math.sqrt(point.radius));
    if (nearestWater === null || point.radius < nearestWater) nearestWater = point.radius;
    if (point.radius >= outerRadius) exposed.push(point.bearing);
  });

  if (usable === 0) throw new ShorelineError('the elevation service returned no usable samples');
  if (!waterBearings.length) {
    throw new ShorelineError(
      `no sea within ${(outerRadius / 1000).toFixed(0)} km - this looks like an inland location`
    );
  }

  const facing = meanBearing(waterBearings, waterWeights);
  if (facing === null) {
    throw new ShorelineError('water surrounds this point evenly - set the facing by hand');
  }

  const openFraction = waterBearings.length / usable;
  return {
    facing,
    confidence: confidenceFor(waterBearings, openFraction, nearestWater),
    openWaterFraction: openFraction,
    nearestWaterM: nearestWater,
    exposedBearings: [...new Set(exposed)].sort((a, b) => a - b),
    source: 'terrain',
  };
}

function confidenceFor(waterBearings, openFraction, nearestWater) {
  const geometry = 1 - Math.min(1, Math.abs(openFraction - 0.5) / 0.42);
  const spread = bearingSpread(waterBearings);
  const coherence = 1 - Math.min(1, Math.abs(spread - 0.36) / 0.36);
  let proximity = 1;
  if (nearestWater === null) proximity = 0;
  else if (nearestWater > 1500) proximity = Math.max(0.15, 1 - (nearestWater - 1500) / 4000);
  const score = 0.45 * geometry + 0.35 * coherence + 0.2 * proximity;
  return Math.round(Math.max(0.05, Math.min(1, score)) * 1000) / 1000;
}
