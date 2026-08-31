# Beach flag predictor

Tells you which safety flag a beach is likely to be flying — green, yellow, red
or double red — from your location and live marine, tide and weather models.
Then it tells you *why*, in the terms that actually decide it: how big the waves
break, how fast the rip channels are running, which way the water is dragging
you down the beach.

Runs on Windows, macOS and Linux. **Python 3.10+, no dependencies, no API key,
no signup.**

> This is a model, not a lifeguard. Where a real flag is flying, that flag wins.
> See [Limits](#limits) before you rely on it.

---

## Quick start

```sh
# macOS / Linux
./beach.sh "Bondi Beach"

# Windows
beach.bat "Bondi Beach"

# or directly
python3 -m beachflag "Bondi Beach"
```

Other ways to say where you are:

```sh
python3 -m beachflag                            # approximate, from your IP
python3 -m beachflag --lat 26.14 --lon -80.10   # exact coordinates
python3 -m beachflag --serve                    # local web app, real GPS
python3 -m beachflag --demo storm               # offline sample day, no network
```

`--serve` is the one to use on a phone or laptop at the beach: it runs a small
server on localhost, opens a page, and asks the browser for your actual
position. Your coordinates go from your browser to that local process and out
to Open-Meteo, and nowhere else.

## What it looks like

```
  🟥  RED FLAG
     High hazard - strong surf and/or currents

  Anglet, Nouvelle-Aquitaine, France
  Mon 31 Aug 14:00 Europe/Paris - beach faces WNW (290 deg) - sandy bar-and-rip - confidence 87%

  Driven by rip currents: it is the reason to stay out of the water.
  Swimming is not advised. Wade no deeper than your knees, if at all.

  WHY
    █████████░ Rip currents       92/100
        Rip channels running about 0.9 m/s, pulsing to 1.5 m/s - faster than
        you can swim against. Beach state: intermediate (bar and rip).
    ███████░░░ Surf               68/100
        1.9 m spilling breakers from a 1.3 m / 10 s swell; surf zone about
        83 m wide.

  CONDITIONS
    Breaking surf    1.9 m spilling
    Swell            1.3 m at 10 s from WNW (+2 deg off shore-normal)
    Wave power       9 kW/m of beach
    Tide             low-mid, falling (range 3.4 m)
    ...

  NEXT HOURS
     14 15 16 17 18 19 20 21 22 23  0  1
      █  █  █  █  █  █  █  █  █  █  █  █

  BEST WINDOW  17:00-19:00 today - the longest calm stretch ahead.
```

---

## What goes into the calculation

### Where you are, and which way the beach looks

Every wave and wind term is measured relative to the **shore normal** — the
bearing the beach looks out along — so getting that right matters more than any
other local parameter. The app works it out from terrain: it samples the digital
elevation model on four rings around you (96 points, one API call), calls
everything at or below sea level water, and takes the circular mean of the
bearings that landed in water.

The same samples also say how sheltered the spot is, and which swell directions
have a headland in the way. If you are on a pier, a headland or in a tight bay
the estimate gets uncertain, the app says so, and `--facing 290` overrides it.

### Sea state

| Input | Why it matters |
| --- | --- |
| Significant wave height | The headline number, but not the one you swim in |
| Wave period | A 1 m 12 s groundswell breaks far heavier than 1 m of 4 s chop |
| Wave direction | Shore-normal swell builds rip cells; oblique swell builds longshore drift |
| Swell / wind-wave split | The app picks whichever partition actually carries the energy |
| Sea surface temperature | Below 15 °C, cold shock is the drowning mechanism, not the surf |
| Ocean current | Background set, on top of anything the surf does |
| Modelled tide height | Turned into a phase: 0 at local low, 1 at local high |

### Weather

Wind speed, gusts and direction (split into onshore and alongshore components in
the beach's own frame), air and apparent temperature, UV index, CAPE,
precipitation and its probability, WMO weather code, visibility, and daylight.
Two days of rainfall history come back with the forecast, for the 24-hour and
48-hour totals that drive the runoff advisory.

### The beach itself

`--beach` picks a profile, which sets the slope, the sand grain size and how
rip-prone the morphology is: `sandy` (default, bars and rip channels),
`dissipative`, `steep`, `reef`, `sheltered`.

### Who is swimming

`--swimmer strong|average|weak|child|nonswimmer` changes the advice and the
comparison against rip speed. It never changes the flag — the flag describes the
water, not you.

---

## How the flag is decided

Offshore forecast values are not what you swim in, so the model runs them
through the standard coastal-engineering relations first:

| Step | Relation |
| --- | --- |
| Breaking wave height | Komar & Gaughan (1972), `Hb = 0.39 g^0.2 (T H0²)^0.4` |
| Refraction to the break point | Snell's law with linear-theory celerity |
| Longshore current | Komar (1979), `V = 1.17 √(g Hb) sin αb cos αb` |
| Breaker type | Iribarren / surf similarity number (Battjes 1974) |
| Beach state | Dimensionless fall velocity Ω (Wright & Short 1984) |
| Wave power | `P = ρg²Hs²Tp / 64π` |

Then ten hazards are scored independently, each on its own 0–100 scale, and each
stating the lowest flag it alone would justify:

**rip currents** · **surf** · **shore break** · **longshore drift** · **wind** ·
**ocean current** · **water temperature** · **weather** · **water quality** ·
**marine life**

The flag is the **worst** of those demands — a red-level rip risk is a red flag
whatever the average says. On top of that, one escalation rule: several moderate
hazards at once (big-ish surf *and* a stiff onshore *and* a falling tide *and*
cold water) is worse than any of them alone, so a high combined index with two
or more yellow-level demands escalates to red.

Marine life carries zero weight by design: a purple flag flies *beside* the
hazard flag, never instead of it. The same goes for the no-lifeguard, water
quality, UV, cold water, offshore wind and lightning advisories.

**Rip currents** get the heaviest weight because they cause the large majority
of surf-beach rescues and drownings. The rip term is driven by breaking wave
energy, how near shore-normal the swell arrives, wave period, tide phase (low
and falling is the dangerous window), sustained onshore wind, and whether the
beach is currently in a state that can hold rip channels at all.

**Confidence** falls with missing inputs, an uncertain shoreline estimate, and
forecast lead time, and is printed with every answer.

---

## Options

```
  place                  beach or town name to look up

  --lat / --lon          exact coordinates
  --facing DEG           bearing the beach looks out along; skips terrain detection
  --beach PROFILE        sandy | dissipative | steep | reef | sheltered

  --at TIME              ISO time, HH:MM today, or +Nh / +Nd from now
  --hours N              hours of timeline to show (default 18)
  --days N               forecast days to fetch (default 4)
  --patrol HH:MM-HH:MM   lifeguard hours, for the no-lifeguard advisory

  --swimmer WHO          strong | average | weak | child | nonswimmer
  --marine-life yes|no   override the jellyfish heuristic with what the beach reported

  --json                 full assessment as JSON
  --units metric|imperial
  --no-color
  --demo SCENARIO        offline sample day        --list-demos to see them
  --serve [PORT]         local web app with browser GPS (default 8765)
```

The **exit code is the flag level** — 0 green, 1 yellow, 2 red, 3 double red — so
it drops straight into a script:

```sh
python3 -m beachflag "Fistral Beach" --json > today.json || echo "not a swimming day"
```

## Data sources

Everything comes from [Open-Meteo](https://open-meteo.com), which is free for
non-commercial use and needs no key:

- `marine-api.open-meteo.com` — waves, swell, sea surface temperature, currents, tide
- `api.open-meteo.com/v1/forecast` — wind, temperature, UV, CAPE, visibility, rainfall
- `api.open-meteo.com/v1/elevation` — terrain, for the shoreline orientation
- `geocoding-api.open-meteo.com` — place name lookup

Responses are cached for 10 minutes (terrain for 30 days) under
`~/.cache/beachflag`, so re-running costs nothing. `BEACHFLAG_CACHE_DIR` moves it.

## Limits

The model cannot see the things a lifeguard sees best, and you should read every
answer with that in mind:

- **Today's sandbars.** Rip channels move after every storm. The model infers
  morphology from wave conditions; it has never looked at this beach.
- **Local rules and closures.** Pollution incidents, shark sightings, jellyfish
  blooms, events, works. A double red for any of those will not appear here.
- **Marine life** is a seasonal-and-wind heuristic, not an observation. No free
  API reports stings. Use `--marine-life` to tell the model what the beach says.
- **Forecast models are coarse.** Marine grids are kilometres wide. A beach
  inside a bay, behind a reef, or at a river mouth can behave nothing like its
  grid cell.
- **It has no idea who is in the water.** Green does not mean safe for a
  non-swimmer, a child alone, or anyone after a drink.

Where a real flag is flying, that flag wins. Swim between the flags, near a
lifeguard, and not alone.

## Layout

```
beachflag/
  fetch.py       stdlib JSON-over-HTTPS with retry and a TTL cache
  geo.py         coordinates, place lookup, circular bearing arithmetic
  shoreline.py   which way the beach faces, derived from terrain
  sources.py     Open-Meteo clients; unit normalisation; tide phasing
  physics.py     surf-zone relations - pure functions, no I/O
  model.py       hazard scoring and the flag decision
  flags.py       ISO 20712 / ILS flag colours and meanings
  render.py      terminal report and JSON payload
  web.py         local web app with browser geolocation
  demo.py        offline sample days, also the model's test fixtures
  cli.py         argument parsing
```

Run the tests with:

```sh
python3 -m unittest discover -s tests -p "test_beachflag*.py"
```
