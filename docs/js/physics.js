// Surf-zone physics. A faithful port of beachflag/physics.py - the two are held
// together by tests/test_beachflag_parity.py, which runs both over the same
// fixtures and compares. Change one, change the other.
//
// Sources: Komar & Gaughan (1972), Komar (1979), Battjes (1974),
// Wright & Short (1984).

export const G = 9.80665;
export const RHO_SEAWATER = 1025.0;
export const BREAKER_INDEX = 0.78;

export const BEACH_PROFILES = {
  dissipative: { name: 'dissipative sand', slope: 0.015, fallVelocity: 0.02,
                 ripChannels: 1.15, tidalSensitivity: 1.3 },
  sandy:       { name: 'sandy bar-and-rip', slope: 0.03, fallVelocity: 0.03,
                 ripChannels: 1.0, tidalSensitivity: 1.0 },
  steep:       { name: 'steep / shingle', slope: 0.10, fallVelocity: 0.07,
                 ripChannels: 0.35, tidalSensitivity: 0.6 },
  reef:        { name: 'reef / rock platform', slope: 0.05, fallVelocity: 0.05,
                 ripChannels: 0.9, tidalSensitivity: 1.2 },
  sheltered:   { name: 'sheltered bay', slope: 0.04, fallVelocity: 0.03,
                 ripChannels: 0.2, tidalSensitivity: 0.4 },
};

export const DEFAULT_PROFILE = BEACH_PROFILES.sandy;

const rad = (deg) => (deg * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;

export function deepWaterWavelength(period) {
  return (G * period * period) / (2 * Math.PI);
}

export function deepWaterCelerity(period) {
  return (G * period) / (2 * Math.PI);
}

export function waveEnergyFlux(height, period) {
  if (height <= 0 || period <= 0) return 0;
  return (RHO_SEAWATER * G * G * height * height * period) / (64 * Math.PI) / 1000;
}

export function breakerHeight(deepHeight, period) {
  if (deepHeight <= 0 || period <= 0) return 0;
  return 0.39 * Math.pow(G, 0.2) * Math.pow(period * deepHeight * deepHeight, 0.4);
}

export function breakerDepth(breakHeight) {
  return breakHeight > 0 ? breakHeight / BREAKER_INDEX : 0;
}

export function surfZoneWidth(breakHeight, slope) {
  if (breakHeight <= 0 || slope <= 0) return 0;
  return breakerDepth(breakHeight) / slope;
}

export function breakerAngle(deepAngleDeg, period, breakHeight) {
  if (period <= 0 || breakHeight <= 0) return 0;
  const deepAngle = Math.max(-89.9, Math.min(89.9, deepAngleDeg));
  const celerityBreak = Math.sqrt(G * breakerDepth(breakHeight));
  const ratio = celerityBreak / deepWaterCelerity(period);
  const sinB = Math.sin(rad(deepAngle)) * Math.min(1, ratio);
  return deg(Math.asin(Math.max(-1, Math.min(1, sinB))));
}

export function longshoreCurrent(breakHeight, breakAngleDeg) {
  if (breakHeight <= 0) return 0;
  const angle = rad(breakAngleDeg);
  return 1.17 * Math.sqrt(G * breakHeight) * Math.abs(Math.sin(angle) * Math.cos(angle));
}

export function iribarren(slope, breakHeight, period) {
  if (breakHeight <= 0 || period <= 0 || slope <= 0) return 0;
  return slope / Math.sqrt(breakHeight / deepWaterWavelength(period));
}

export function breakerType(iribarrenNumber) {
  if (iribarrenNumber <= 0) return 'no surf';
  if (iribarrenNumber < 0.4) return 'spilling';
  if (iribarrenNumber < 2.0) return 'plunging';
  return 'surging';
}

export function dimensionlessFallVelocity(breakHeight, period, fallVelocity) {
  if (breakHeight <= 0 || period <= 0 || fallVelocity <= 0) return 0;
  return breakHeight / (fallVelocity * period);
}

export function beachState(omega) {
  if (omega <= 0) return 'no surf';
  if (omega < 1.0) return 'reflective';
  if (omega < 6.0) return 'intermediate (bar and rip)';
  return 'dissipative';
}

export function ripMorphologyFactor(omega) {
  if (omega <= 0) return 0;
  if (omega < 1.0) return 0.25 * omega;
  if (omega <= 5.0) return 0.25 + 0.75 * Math.min(1, (omega - 1.0) / 2.0);
  return Math.max(0.45, 1.0 - (omega - 5.0) / 10.0);
}

export function ripSpeed(breakHeight, morphology, forcing) {
  if (breakHeight <= 0) return 0;
  const scale = 0.20 * Math.sqrt(G * breakHeight);
  return scale * Math.max(0, Math.min(1, morphology)) * Math.max(0, Math.min(1.2, forcing));
}

export function windComponents(speed, windFromDeg, facingDeg) {
  const delta = rad(windFromDeg - facingDeg);
  return [speed * Math.cos(delta), speed * Math.sin(delta)];
}
