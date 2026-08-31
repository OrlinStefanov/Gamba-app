// Runs the JavaScript model over a fixture handed in on stdin and prints the
// result as JSON, so test_beachflag_parity.py can compare it against the
// Python model's answer for the same inputs.

import { build, hourAt } from '../docs/js/sources.js';
import { assumedShoreline } from '../docs/js/shoreline.js';
import { predict } from '../docs/js/model.js';
import { BEACH_PROFILES } from '../docs/js/physics.js';
import { SWIMMERS } from '../docs/js/flags.js';

const input = JSON.parse(await new Promise((resolve) => {
  let raw = '';
  process.stdin.on('data', (chunk) => { raw += chunk; });
  process.stdin.on('end', () => resolve(raw));
}));

const forecast = build(input.marine, input.weather);
const shore = assumedShoreline(input.facing);
const options = {
  profile: BEACH_PROFILES[input.profile || 'sandy'],
  swimmer: SWIMMERS[input.swimmer || 'average'],
  marineLife: input.marineLife || null,
};

const out = input.stamps.map((stamp) => {
  const hour = hourAt(forecast, stamp);
  if (!hour) return { stamp, missing: true };
  const p = predict(hour, shore, options);
  return {
    stamp,
    time: p.time,
    flag: p.flag,
    score: p.score,
    confidence: p.confidence,
    headline: p.headline,
    advisories: p.advisories,
    drivers: p.drivers.map((d) => ({
      key: d.key, score: d.score, demand: d.demand, detail: d.detail,
    })),
    metrics: p.metrics,
    hour: {
      tidePhase: hour.tidePhase, tideRising: hour.tideRising, tideRange: hour.tideRange,
      rain24h: hour.rain24h, rain48h: hour.rain48h, currentSpeed: hour.currentSpeed,
    },
  };
});

process.stdout.write(JSON.stringify(out));
