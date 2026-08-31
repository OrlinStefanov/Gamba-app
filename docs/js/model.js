// Hazard scoring and the flag decision. Port of beachflag/model.py.
//
// Held to the Python original by tests/test_beachflag_parity.py, which runs both
// over the same fixtures and compares flags, scores and every driver sentence.

import * as physics from './physics.js';
import { compassPoint, wrap180 } from './geo.js';
import { FLAG, FLAG_INFO, SWIMMERS } from './flags.js';
import { shelterFactor } from './shoreline.js';
import { dominantSwell, futureHours, isThunderstorm } from './sources.js';

export const DEFAULT_PATROL = [600, 1080]; // 10:00-18:00 as minutes of the day.

export const WEIGHTS = {
  rip: 0.30,
  surf: 0.22,
  shorebreak: 0.10,
  longshore: 0.10,
  wind: 0.08,
  current: 0.06,
  thermal: 0.07,
  storm: 0.05,
  quality: 0.02,
  marine_life: 0.00, // Advisory only: never moves the hazard flag.
};

export const clamp = (v, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, v));

export function ramp(value, start, end) {
  if (end <= start) return value >= end ? 1 : 0;
  return clamp((value - start) / (end - start));
}

export function ladder(value, yellow, red, doubleRed) {
  if (value >= doubleRed) return FLAG.DOUBLE_RED;
  if (value >= red) return FLAG.RED;
  if (value >= yellow) return FLAG.YELLOW;
  return FLAG.GREEN;
}

const rad = (d) => (d * Math.PI) / 180;
const round1 = (v) => Math.round(v * 10) / 10;
const fixed = (v, places) => v.toFixed(places);
const signed = (v) => (v >= 0 ? '+' : '') + v.toFixed(0);

// ---------------------------------------------------------------------------
// derived physics for one hour
// ---------------------------------------------------------------------------

export function derive(hour, shore, profile) {
  const [height, period, direction] = dominantSwell(hour);
  let onshore = 0;
  let alongshore = 0;
  if (hour.windSpeed !== null && hour.windDirection !== null) {
    [onshore, alongshore] = physics.windComponents(hour.windSpeed, hour.windDirection, shore.facing);
  }

  const empty = {
    breakerHeight: 0, breakerType: 'no surf', breakerAngle: 0,
    deepHeight: 0, deepPeriod: 0, deepDirection: null,
    wavePower: 0, surfZoneWidth: 0, iribarren: 0, omega: 0, beachState: 'no surf',
    longshoreCurrent: 0, longshoreToward: null, ripSpeed: 0, ripPeak: 0,
    onshoreWind: onshore, alongshoreWind: alongshore, shelter: 1, incidence: 0,
  };
  if (!height || !period) return empty;

  const shelter = direction === null ? 1 : shelterFactor(shore, direction);
  const incidence = direction === null ? 0 : wrap180(direction - shore.facing);

  // Energy flux onto a straight shoreline falls as cos(approach), so the
  // equivalent shore-normal height scales as its square root.
  const obliquity = Math.sqrt(Math.max(0, Math.cos(rad(Math.min(Math.abs(incidence), 89)))));
  const effectiveHeight = height * shelter * obliquity;

  const breakHeight = physics.breakerHeight(effectiveHeight, period);
  const breakAngle = physics.breakerAngle(incidence, period, breakHeight);
  const omega = physics.dimensionlessFallVelocity(breakHeight, period, profile.fallVelocity);
  const morphology = physics.ripMorphologyFactor(omega) * profile.ripChannels;
  const forcing = ripForcing(hour, breakHeight, period, incidence, onshore, profile);
  const meanRip = physics.ripSpeed(breakHeight, morphology, forcing);
  const longshore = physics.longshoreCurrent(breakHeight, breakAngle);

  let driftToward = null;
  if (longshore > 0.05 && Math.abs(breakAngle) > 0.5) {
    // Waves from the right of the shore normal drive drift to the left.
    driftToward = ((shore.facing - 90 * (incidence > 0 ? 1 : -1)) % 360 + 360) % 360;
  }

  const iribarrenNumber = physics.iribarren(profile.slope, breakHeight, period);
  return {
    breakerHeight: breakHeight,
    breakerType: physics.breakerType(iribarrenNumber),
    breakerAngle: breakAngle,
    deepHeight: height,
    deepPeriod: period,
    deepDirection: direction,
    wavePower: physics.waveEnergyFlux(effectiveHeight, period),
    surfZoneWidth: physics.surfZoneWidth(breakHeight, profile.slope),
    iribarren: iribarrenNumber,
    omega,
    beachState: physics.beachState(omega),
    longshoreCurrent: longshore,
    longshoreToward: driftToward,
    ripSpeed: meanRip,
    ripPeak: meanRip * 1.6,
    onshoreWind: onshore,
    alongshoreWind: alongshore,
    shelter,
    incidence,
  };
}

function ripForcing(hour, breakHeight, period, incidence, onshore, profile) {
  const energy = ramp(breakHeight, 0.25, 1.6);
  const normality = clamp(Math.pow(Math.cos(rad(Math.min(Math.abs(incidence), 90))), 2), 0.15, 1);
  const periodGain = 0.65 + 0.35 * ramp(period, 5, 12);

  let tideGain = 1;
  if (hour.tidePhase !== null) {
    const lowBonus = (1 - hour.tidePhase) * 0.45 * profile.tidalSensitivity;
    const fallingBonus = hour.tideRising === false ? 0.1 * profile.tidalSensitivity : 0;
    tideGain = 1 + lowBonus + fallingBonus;
  }

  const windGain = 1 + 0.15 * ramp(onshore, 3, 12);
  return clamp(energy * normality * periodGain * tideGain * windGain, 0, 1.2);
}

// ---------------------------------------------------------------------------
// hazard drivers
// ---------------------------------------------------------------------------

const driver = (key, label, score, demand, detail) => ({ key, label, score, demand, detail });

function surfDriver(m) {
  const hb = m.breakerHeight;
  const score = 100 * ramp(hb, 0.15, 2.8);
  const demand = ladder(hb, 0.55, 1.25, 2.6);
  let detail;
  if (hb < 0.1) {
    detail = 'Flat - no meaningful surf.';
  } else {
    detail = `${fixed(hb, 1)} m ${m.breakerType} breakers from a ${fixed(m.deepHeight, 1)} m / `
      + `${fixed(m.deepPeriod, 0)} s swell; surf zone about ${fixed(m.surfZoneWidth, 0)} m wide.`;
    if (m.shelter < 0.5) detail += ' Mostly blocked by land, so much of that swell never arrives.';
  }
  return driver('surf', 'Surf', score, demand, detail);
}

function shorebreakDriver(m, profile) {
  if (m.breakerHeight < 0.3 || m.iribarren < 0.6) {
    return driver('shorebreak', 'Shore break', 0, FLAG.GREEN, 'No significant shore dump.');
  }
  const severity = ramp(m.iribarren, 0.6, 2.2) * ramp(m.breakerHeight, 0.3, 1.6);
  return driver('shorebreak', 'Shore break', 100 * severity, ladder(severity, 0.25, 0.6, 1.01),
    `Waves are ${m.breakerType} onto a ${profile.name} profile (Iribarren ${fixed(m.iribarren, 1)}) `
    + '- a dumping shore break that injures necks and shoulders.');
}

function ripDriver(m, swimmer) {
  const peak = m.ripPeak;
  const score = 100 * ramp(peak, 0.15, 1.6);
  const demand = ladder(peak, 0.35, 0.8, 1.6);
  let detail;
  if (peak < 0.15) {
    detail = 'Little to drive rip currents.';
  } else {
    const comparison = peak > swimmer.swimSpeed
      ? 'faster than you can swim against'
      : 'within what you could swim against, briefly';
    detail = `Rip channels running about ${fixed(m.ripSpeed, 1)} m/s, pulsing to ${fixed(peak, 1)} m/s `
      + `- ${comparison}. Beach state: ${m.beachState}.`;
  }
  return driver('rip', 'Rip currents', score, demand, detail);
}

function longshoreDriver(m) {
  const v = m.longshoreCurrent;
  const score = 100 * ramp(v, 0.15, 1.1);
  const demand = ladder(v, 0.3, 0.7, 1.3);
  let detail;
  if (v < 0.15) {
    detail = 'No appreciable longshore drift.';
  } else {
    const toward = m.longshoreToward !== null ? ` toward the ${compassPoint(m.longshoreToward)}` : '';
    detail = `Longshore current about ${fixed(v, 1)} m/s${toward} (breakers arriving at `
      + `${fixed(Math.abs(m.breakerAngle), 0)} deg) - it will walk you down the beach.`;
  }
  return driver('longshore', 'Longshore drift', score, demand, detail);
}

function windDriver(hour, m) {
  const gusts = hour.windGusts || hour.windSpeed || 0;
  const speed = hour.windSpeed || 0;
  const score = 100 * Math.max(ramp(gusts, 8, 22), ramp(Math.abs(m.onshoreWind), 6, 18));
  let demand = ladder(gusts, 11, 18, 26);
  let detail;

  if (speed < 2) {
    detail = 'Light and variable wind.';
  } else if (m.onshoreWind < -4) {
    detail = `${fixed(Math.abs(m.onshoreWind), 0)} m/s offshore wind: it flattens the surf but pushes `
      + 'anything floating - inflatables, boards, air beds - straight out to sea.';
    demand = Math.max(demand, FLAG.YELLOW);
  } else if (m.onshoreWind > 8) {
    detail = `${fixed(m.onshoreWind, 0)} m/s onshore wind piling up choppy, disorganised surf.`;
  } else {
    const direction = hour.windDirection !== null ? compassPoint(hour.windDirection) : '?';
    detail = `${fixed(speed, 0)} m/s wind from the ${direction}, gusting ${fixed(gusts, 0)} m/s.`;
  }
  return driver('wind', 'Wind', score, demand, detail);
}

function currentDriver(hour) {
  const speed = hour.currentSpeed || 0;
  const score = 100 * ramp(speed, 0.2, 1.2);
  const demand = ladder(speed, 0.4, 0.9, 1.6);
  let detail;
  if (speed < 0.15) {
    detail = 'Ambient current is negligible.';
  } else {
    const toward = hour.currentDirection !== null ? ` setting ${compassPoint(hour.currentDirection)}` : '';
    detail = `Background ocean current ${fixed(speed, 1)} m/s${toward}, on top of anything the surf does.`;
  }
  return driver('current', 'Ocean current', score, demand, detail);
}

function thermalDriver(hour, swimmer) {
  const sst = hour.seaTemperature;
  if (sst === null) {
    return driver('thermal', 'Water temperature', 0, FLAG.GREEN, 'No sea temperature available.');
  }
  const score = 100 * ramp(18 - sst, 0, 12);
  let demand = FLAG.GREEN;
  if (sst < 10) demand = FLAG.RED;
  else if (sst < 15) demand = FLAG.YELLOW;

  let detail;
  if (sst >= 22) detail = `Sea ${fixed(sst, 0)} C - comfortable.`;
  else if (sst >= 18) detail = `Sea ${fixed(sst, 0)} C - brisk but fine for a swim.`;
  else if (sst >= 15) detail = `Sea ${fixed(sst, 0)} C - cold enough to shorten how long you last in it.`;
  else if (sst >= 10) detail = `Sea ${fixed(sst, 0)} C - cold shock territory. Enter slowly, or wear a wetsuit.`;
  else detail = `Sea ${fixed(sst, 0)} C - cold water shock is the real hazard here, whatever the surf does.`;

  if (sst < swimmer.coldTolerance) {
    detail += ` Below what an unprotected ${swimmer.label} should be entering.`;
  }
  if (hour.airTemperature !== null && hour.airTemperature - sst > 12) {
    detail += ' Hot air over cold water makes it feel far milder than it is.';
  }
  return driver('thermal', 'Water temperature', score, demand, detail);
}

function stormDriver(hour) {
  if (isThunderstorm(hour)) {
    return driver('storm', 'Thunderstorms', 100, FLAG.DOUBLE_RED,
      'Thunderstorms overhead. Water is cleared for lightning - open water is the worst place to be, '
      + 'and beaches close for this even in flat calm.');
  }
  const cape = hour.cape || 0;
  const probability = hour.precipitationProbability || 0;
  const instability = ramp(cape, 800, 2500) * ramp(probability, 30, 80);
  const visibility = hour.visibility;

  let score = 100 * instability;
  let demand = FLAG.GREEN;
  const details = [];
  if (instability > 0.45) {
    demand = FLAG.YELLOW;
    details.push(`Unstable air (CAPE ${fixed(cape, 0)} J/kg, ${fixed(probability, 0)}% rain) `
      + '- thunderstorms could build with little warning.');
  }
  if (visibility !== null && visibility < 2000) {
    score = Math.max(score, 100 * ramp(2000 - visibility, 0, 1500));
    demand = Math.max(demand, visibility < 1000 ? FLAG.YELLOW : FLAG.GREEN);
    details.push(`Visibility down to ${fixed(visibility / 1000, 1)} km - a swimmer in trouble is hard to spot.`);
  }
  if (!details.length) details.push('Settled weather.');
  return driver('storm', 'Weather', score, demand, details.join(' '));
}

function waterQualityDriver(hour) {
  if (hour.rain24h === null && hour.rain48h === null) {
    return driver('quality', 'Water quality', 0, FLAG.GREEN, 'No rainfall history available.');
  }
  const r24 = hour.rain24h || 0;
  const r48 = hour.rain48h || 0;
  const score = 100 * Math.max(ramp(r24, 8, 40), ramp(r48, 15, 60));
  const demand = score >= 60 ? FLAG.YELLOW : FLAG.GREEN;
  const detail = score < 20
    ? 'No significant recent rain, so runoff is unlikely to be an issue.'
    : `${fixed(r24, 0)} mm of rain in the last 24 h (${fixed(r48, 0)} mm over 48 h). Storm drains and `
      + 'rivers push bacteria onto beaches for a day or two after this - many services post a swim advisory.';
  return driver('quality', 'Water quality', score, demand, detail);
}

function marineLifeDriver(hour, m, override) {
  if (override === 'yes') {
    return driver('marine_life', 'Marine life', 80, FLAG.GREEN,
      'Dangerous marine life reported (you told the model so).');
  }
  if (override === 'no') {
    return driver('marine_life', 'Marine life', 0, FLAG.GREEN,
      'No marine life reported (you told the model so).');
  }
  const sst = hour.seaTemperature;
  if (sst === null || sst < 20) {
    return driver('marine_life', 'Marine life', 0, FLAG.GREEN,
      'Water too cool for the usual drifting stingers.');
  }
  const likelihood = ramp(sst, 20, 27) * ramp(m.onshoreWind, 3, 10);
  const score = 60 * likelihood;
  if (likelihood < 0.35) {
    return driver('marine_life', 'Marine life', score, FLAG.GREEN,
      'Nothing in the conditions particularly favours stingers today.');
  }
  return driver('marine_life', 'Marine life', score, FLAG.GREEN,
    `Warm water (${fixed(sst, 0)} C) with a steady onshore wind is what blows jellyfish and man-o-war `
    + 'ashore. Check the sand and the local notice board.');
}

// ---------------------------------------------------------------------------
// aggregation
// ---------------------------------------------------------------------------

export function predict(hour, shore, options = {}) {
  const profile = options.profile || physics.DEFAULT_PROFILE;
  const swimmer = options.swimmer || SWIMMERS.average;
  const patrol = options.patrol || DEFAULT_PATROL;
  const marineLife = options.marineLife || null;
  const leadHours = options.leadHours || 0;

  const m = derive(hour, shore, profile);
  const drivers = [
    ripDriver(m, swimmer),
    surfDriver(m),
    shorebreakDriver(m, profile),
    longshoreDriver(m),
    windDriver(hour, m),
    currentDriver(hour),
    thermalDriver(hour, swimmer),
    stormDriver(hour),
    waterQualityDriver(hour),
    marineLifeDriver(hour, m, marineLife),
  ];
  const byKey = Object.fromEntries(drivers.map((d) => [d.key, d]));

  // Only weighted drivers can move the hazard flag. Advisory-only terms raise
  // their own flag beside it and nothing more.
  const hazards = drivers.filter((d) => (WEIGHTS[d.key] || 0) > 0);
  const totalWeight = hazards.reduce((sum, d) => sum + WEIGHTS[d.key], 0);
  let score = hazards.reduce((sum, d) => sum + WEIGHTS[d.key] * d.score, 0) / totalWeight;
  score = round1(Math.min(100, score));

  let flag = Math.max(...hazards.map((d) => d.demand));
  const moderate = hazards.filter((d) => d.demand >= FLAG.YELLOW).length;
  if (flag < FLAG.RED && score >= 62 && moderate >= 2) flag = FLAG.RED;
  else if (flag < FLAG.YELLOW && score >= 34) flag = FLAG.YELLOW;

  return {
    time: hour.time,
    isDay: hour.isDay,
    flag,
    score,
    confidence: confidence(hour, shore, leadHours),
    drivers,
    advisories: advisoriesFor(hour, m, byKey, patrol),
    metrics: m,
    headline: headlineFor(flag, drivers, m),
    reasons: ranked(drivers).filter(isNotable).slice(0, 4).map((d) => d.detail),
  };
}

const isNotable = (d) => d.score >= 20 || d.demand > FLAG.GREEN;

function ranked(drivers) {
  // Stable sort, so ties keep the declaration order - as Python's does.
  return [...drivers].sort((a, b) => (b.demand - a.demand) || (b.score - a.score));
}

export function topDrivers(prediction, limit = 4) {
  return ranked(prediction.drivers).filter(isNotable).slice(0, limit);
}

function advisoriesFor(hour, m, byKey, patrol) {
  const found = [];
  if (isThunderstorm(hour)) found.push('lightning');
  if (byKey.marine_life.score >= 20) found.push('purple');
  if (byKey.quality.score >= 35) found.push('water_quality');
  if (hour.seaTemperature !== null && hour.seaTemperature < 15) found.push('cold_water');
  if (m.onshoreWind <= -4) found.push('offshore_wind');
  if ((hour.uvIndex || 0) >= 8) found.push('uv_extreme');

  const minutes = hour.hourOfDay * 60;
  if (minutes < patrol[0] || minutes >= patrol[1] || hour.isDay === false) {
    found.push('no_lifeguard');
  }
  return [...new Set(found)];
}

function headlineFor(flag, drivers, m) {
  let deciding = null;
  for (const d of drivers) {
    if ((WEIGHTS[d.key] || 0) <= 0) continue;
    if (deciding === null
        || d.demand > deciding.demand
        || (d.demand === deciding.demand && d.score > deciding.score)) {
      deciding = d;
    }
  }
  if (flag === FLAG.GREEN) {
    if (m.breakerHeight < 0.15) return 'Flat and calm - about as benign as a beach gets.';
    return `Small ${fixed(m.breakerHeight, 1)} m surf and nothing else of note.`;
  }
  const subjects = {
    rip: 'rip currents', surf: 'the surf', shorebreak: 'the shore break',
    longshore: 'longshore drift', wind: 'the wind', current: 'the ocean current',
    thermal: 'the water temperature', storm: 'the weather', quality: 'water quality',
  };
  const verbs = {
    [FLAG.YELLOW]: 'makes this a swim-with-care day',
    [FLAG.RED]: 'is the reason to stay out of the water',
    [FLAG.DOUBLE_RED]: 'should have the beach closed',
  };
  const subject = subjects[deciding.key] || deciding.label.toLowerCase();
  return `Driven by ${subject}: it ${verbs[flag]}.`;
}

export function confidence(hour, shore, leadHours) {
  const wanted = [
    hour.waveHeight, hour.wavePeriod, hour.waveDirection,
    hour.windSpeed, hour.windDirection, hour.seaTemperature,
    hour.seaLevel, hour.airTemperature,
  ];
  const completeness = wanted.filter((v) => v !== null && v !== undefined).length / wanted.length;
  const lead = 1 - 0.55 * clamp(leadHours / 120);
  const value = 0.45 * completeness + 0.3 * shore.confidence + 0.25 * lead;
  return Math.round(clamp(value) * 1000) / 1000;
}

export function predictSeries(forecast, shore, startStamp, count, options = {}) {
  const start = Date.parse(startStamp + 'Z');
  return futureHours(forecast, startStamp, count).map((hour) => {
    const lead = Math.max(0, (Date.parse(hour.time + 'Z') - start) / 3600000);
    return predict(hour, shore, { ...options, leadHours: lead });
  });
}

export function bestWindow(predictions, maxFlag = FLAG.YELLOW, daylightOnly = true) {
  let best = null;
  let bestLength = 0;
  let runStart = null;
  let runLength = 0;

  for (const p of predictions) {
    const daylight = !daylightOnly || p.isDay !== false;
    if (p.flag <= maxFlag && daylight) {
      if (runStart === null) { runStart = p.time; runLength = 0; }
      runLength += 1;
      if (runLength > bestLength) { bestLength = runLength; best = [runStart, p.time]; }
    } else {
      runStart = null;
      runLength = 0;
    }
  }
  return bestLength >= 2 ? best : null;
}

export { FLAG, FLAG_INFO, SWIMMERS };
