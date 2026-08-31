// ISO 20712 / ILS flag set. Port of beachflag/flags.py.

export const FLAG = { GREEN: 0, YELLOW: 1, RED: 2, DOUBLE_RED: 3 };
export const FLAG_KEYS = ['green', 'yellow', 'red', 'double_red'];

export const FLAG_INFO = [
  { key: 'green', label: 'GREEN',
    meaning: 'Calm conditions, low hazard',
    advice: "Swimming is fine. Stay between the flags and keep children in arm's reach.",
    color: '#12A150' },
  { key: 'yellow', label: 'YELLOW',
    meaning: 'Moderate hazard - surf, currents or both',
    advice: 'Swim near a lifeguard, stay shallow, and do not go in alone.',
    color: '#E6A700' },
  { key: 'red', label: 'RED',
    meaning: 'High hazard - strong surf and/or currents',
    advice: 'Swimming is not advised. Wade no deeper than your knees, if at all.',
    color: '#D42222' },
  { key: 'double_red', label: 'DOUBLE RED',
    meaning: 'Water closed to the public',
    advice: 'Stay out of the water entirely. Entering may also be an offence here.',
    color: '#8B0000' },
];

export const ADVISORY_INFO = {
  purple:        { label: 'PURPLE', meaning: 'Dangerous marine life may be present', color: '#7B2FBE' },
  no_lifeguard:  { label: 'NO LIFEGUARD', meaning: 'Outside typical patrol hours - nobody is watching the water', color: '#5A6473' },
  water_quality: { label: 'WATER QUALITY', meaning: 'Recent rainfall makes a bacterial advisory likely', color: '#8A6F3D' },
  uv_extreme:    { label: 'UV', meaning: 'Very high or extreme UV - burn time is minutes, not hours', color: '#E0611A' },
  cold_water:    { label: 'COLD WATER', meaning: 'Cold shock risk on entry regardless of how warm the air is', color: '#2E7FCB' },
  offshore_wind: { label: 'OFFSHORE WIND', meaning: 'Wind is blowing off the beach - inflatables and boards will be carried out', color: '#4B9E8F' },
  lightning:     { label: 'LIGHTNING', meaning: 'Thunderstorms in the area - beaches clear the water for these', color: '#B08CE0' },
};

export const SWIMMERS = {
  strong:     { key: 'strong', label: 'strong swimmer', swimSpeed: 1.1, surfTolerance: 1.8, coldTolerance: 12.0 },
  average:    { key: 'average', label: 'average swimmer', swimSpeed: 0.7, surfTolerance: 1.0, coldTolerance: 16.0 },
  weak:       { key: 'weak', label: 'weak swimmer', swimSpeed: 0.4, surfTolerance: 0.5, coldTolerance: 19.0 },
  child:      { key: 'child', label: 'child', swimSpeed: 0.25, surfTolerance: 0.3, coldTolerance: 20.0 },
  nonswimmer: { key: 'nonswimmer', label: 'non-swimmer', swimSpeed: 0.1, surfTolerance: 0.2, coldTolerance: 21.0 },
};
