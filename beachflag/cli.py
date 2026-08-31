"""Command line interface."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from urllib.parse import urlparse

from . import demo as demo_module
from . import model, render, shoreline as shoreline_module, sources
from .fetch import FetchError
from .flags import Flag
from .geo import Location, geocode, locate_by_ip, validate
from .model import SWIMMERS
from .physics import BEACH_PROFILES, DEFAULT_PROFILE
from .render import Style, Units
from .shoreline import Shoreline, assumed_shoreline

EPILOG = """\
examples:
  beachflag                            use your approximate location from IP
  beachflag "Bondi Beach"              look up a place by name
  beachflag --lat 26.14 --lon -80.10   exact coordinates
  beachflag --at +6h --hours 24        six hours from now, with a day's timeline
  beachflag --demo storm               offline sample day, no network needed
  beachflag --serve                    local web app with real GPS from the browser

The flag is an estimate from public marine and weather models. It is not an
official forecast, and where a real flag is flying, that flag wins.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beachflag",
        description="Predict which safety flag a beach is likely to be flying.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("place", nargs="?", help="beach or town name to look up")

    where = parser.add_argument_group("location")
    where.add_argument("--lat", type=float, help="latitude in decimal degrees")
    where.add_argument("--lon", type=float, help="longitude in decimal degrees")
    where.add_argument(
        "--facing",
        type=float,
        metavar="DEG",
        help="compass bearing the beach looks out along; skips terrain detection",
    )
    where.add_argument(
        "--beach",
        choices=sorted(BEACH_PROFILES),
        default="sandy",
        help="beach profile, which sets slope and how rip-prone it is (default: sandy)",
    )

    when = parser.add_argument_group("time")
    when.add_argument("--at", metavar="TIME", help="ISO time, HH:MM today, or +Nh from now")
    when.add_argument("--hours", type=int, default=18, help="hours of timeline to show (default: 18)")
    when.add_argument("--days", type=int, default=4, help="forecast days to fetch (default: 4)")
    when.add_argument(
        "--patrol",
        metavar="HH:MM-HH:MM",
        default="10:00-18:00",
        help="lifeguard patrol hours, for the no-lifeguard advisory",
    )

    who = parser.add_argument_group("who is swimming")
    who.add_argument("--swimmer", choices=sorted(SWIMMERS), default="average",
                     help="tunes the advice, never the flag (default: average)")
    who.add_argument("--marine-life", choices=("yes", "no"), dest="marine_life",
                     help="override the jellyfish heuristic with what the beach actually reports")

    out = parser.add_argument_group("output")
    out.add_argument("--json", action="store_true", help="emit the full assessment as JSON")
    out.add_argument("--units", choices=("metric", "imperial"), default="metric")
    out.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    out.add_argument("--demo", metavar="SCENARIO", help="run an offline sample day")
    out.add_argument("--list-demos", action="store_true", help="list the sample days and exit")
    out.add_argument("--serve", nargs="?", const=8765, type=int, metavar="PORT",
                     help="run the local web app instead (default port 8765)")
    out.add_argument("--no-browser", action="store_true", help="with --serve, do not open a browser")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_demos:
        for key, scenario in demo_module.SCENARIOS.items():
            print(f"  {key:<10} {scenario.title}  ({scenario.expectation})")
        return 0

    if args.serve is not None:
        from .web import serve

        return serve(port=args.serve, open_browser=not args.no_browser)

    try:
        return _run(args)
    except FetchError as exc:
        host = urlparse(exc.url).netloc or exc.url
        print(f"beachflag: {host}: {exc.reason}", file=sys.stderr)
        if exc.status is None:
            print("  The forecast services need internet access. Try --demo for an "
                  "offline sample day.", file=sys.stderr)
        return 2
    except (ValueError, KeyError) as exc:
        print(f"beachflag: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _run(args: argparse.Namespace) -> int:
    profile = BEACH_PROFILES.get(args.beach, DEFAULT_PROFILE)
    swimmer = SWIMMERS[args.swimmer]
    patrol = _parse_patrol(args.patrol)

    if args.demo:
        scenario = demo_module.get(args.demo)
        location = scenario.location
        shore = assumed_shoreline(args.facing if args.facing is not None else scenario.facing)
        forecast = sources.build(*demo_module.payloads(scenario))
        now = sources.now_local(forecast)
    else:
        location = _resolve_location(args)
        shore = _resolve_shoreline(args, location)
        forecast = sources.load(
            location.latitude, location.longitude, forecast_days=max(1, args.days)
        )
        now = sources.now_local(forecast)

    target = _parse_when(args.at, now)
    hour = forecast.at(target)
    if hour is None:
        print(
            f"beachflag: {target:%Y-%m-%d %H:%M} is outside the forecast window "
            f"({forecast.hours[0].time:%Y-%m-%d %H:%M} to {forecast.hours[-1].time:%Y-%m-%d %H:%M}).",
            file=sys.stderr,
        )
        return 2

    shared = dict(profile=profile, swimmer=swimmer, marine_life=args.marine_life, patrol=patrol)
    lead = max(0.0, (hour.time - now).total_seconds() / 3600.0)
    prediction = model.predict(hour, shore, forecast=forecast, lead_hours=lead, **shared)
    timeline = model.predict_series(
        forecast, shore, target, max(0, args.hours), **shared
    )
    window = model.best_window(timeline, max_flag=Flag.YELLOW, forecast=forecast)

    if args.json:
        print(json.dumps(
            render.as_dict(
                prediction,
                location=location,
                shore=shore,
                forecast=forecast,
                profile=profile,
                timeline=timeline,
                window=window,
            ),
            indent=2,
        ))
    else:
        print(render.report(
            prediction,
            location=location,
            shore=shore,
            forecast=forecast,
            profile=profile,
            swimmer=swimmer,
            timeline=timeline,
            window=window,
            units=Units(args.units == "imperial"),
            style=Style(False if args.no_color else None),
        ))

    # Exit code doubles as the flag level, so scripts and cron can act on it.
    return int(prediction.flag)


def _resolve_location(args: argparse.Namespace) -> Location:
    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None:
            raise ValueError("--lat and --lon must be given together")
        lat, lon = validate(args.lat, args.lon)
        return Location(lat, lon, source="coordinates")
    if args.place:
        return geocode(args.place)[0]
    location = locate_by_ip()
    print(
        f"Using your approximate location from your IP address: {location.label()}.\n"
        "That is city-accurate at best - pass a beach name, --lat/--lon, or use "
        "--serve for real GPS.\n",
        file=sys.stderr,
    )
    return location


def _resolve_shoreline(args: argparse.Namespace, location: Location) -> Shoreline:
    if args.facing is not None:
        return assumed_shoreline(args.facing)
    try:
        return shoreline_module.detect(location.latitude, location.longitude)
    except FetchError as exc:
        raise FetchError(
            exc.url,
            f"{exc.reason}. If this is a beach, pass --facing with the bearing it looks out along",
            status=exc.status,
        ) from exc


def _parse_when(raw: str | None, now: datetime) -> datetime:
    if not raw:
        return now.replace(minute=0, second=0, microsecond=0)

    relative = re.fullmatch(r"\+(\d+(?:\.\d+)?)\s*([hd])", raw.strip(), re.IGNORECASE)
    if relative:
        amount = float(relative.group(1))
        delta = timedelta(hours=amount) if relative.group(2).lower() == "h" else timedelta(days=amount)
        return (now + delta).replace(minute=0, second=0, microsecond=0)

    clock = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    if clock:
        return now.replace(hour=int(clock.group(1)), minute=0, second=0, microsecond=0)

    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise ValueError(f"could not read {raw!r} as a time; try 15:00, +6h, or 2026-08-31T15:00") from None
    return (parsed.replace(tzinfo=None) if parsed.tzinfo else parsed).replace(
        minute=0, second=0, microsecond=0
    )


def _parse_patrol(raw: str) -> tuple:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", raw.strip())
    if not match:
        raise ValueError(f"could not read patrol hours {raw!r}; expected HH:MM-HH:MM")
    from datetime import time as dtime

    start = dtime(int(match.group(1)), int(match.group(2)))
    end = dtime(int(match.group(3)), int(match.group(4)))
    return start, end
