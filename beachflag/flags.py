"""The flags themselves.

Colours and meanings follow ISO 20712 / the International Life Saving
Federation set, which is what most of Europe, Australia, New Zealand and the
US flies. Local services vary - some US beaches use a single red where the ILS
set would fly two, and a few countries add their own colours - so the CLI
prints the meaning next to the colour rather than assuming you read it the
same way the model does.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Flag(IntEnum):
    """Primary hazard flags, ordered by severity so they can be compared."""

    GREEN = 0
    YELLOW = 1
    RED = 2
    DOUBLE_RED = 3


@dataclass(frozen=True)
class FlagInfo:
    flag: Flag
    label: str
    meaning: str
    advice: str
    ansi: str
    hex_color: str
    emoji: str


FLAG_INFO: dict[Flag, FlagInfo] = {
    Flag.GREEN: FlagInfo(
        flag=Flag.GREEN,
        label="GREEN",
        meaning="Calm conditions, low hazard",
        advice="Swimming is fine. Stay between the flags and keep children in arm's reach.",
        ansi="\033[1;32m",
        hex_color="#12A150",
        emoji="\N{LARGE GREEN SQUARE}",
    ),
    Flag.YELLOW: FlagInfo(
        flag=Flag.YELLOW,
        label="YELLOW",
        meaning="Moderate hazard - surf, currents or both",
        advice="Swim near a lifeguard, stay shallow, and do not go in alone.",
        ansi="\033[1;33m",
        hex_color="#E6A700",
        emoji="\N{LARGE YELLOW SQUARE}",
    ),
    Flag.RED: FlagInfo(
        flag=Flag.RED,
        label="RED",
        meaning="High hazard - strong surf and/or currents",
        advice="Swimming is not advised. Wade no deeper than your knees, if at all.",
        ansi="\033[1;31m",
        hex_color="#D42222",
        emoji="\N{LARGE RED SQUARE}",
    ),
    Flag.DOUBLE_RED: FlagInfo(
        flag=Flag.DOUBLE_RED,
        label="DOUBLE RED",
        meaning="Water closed to the public",
        advice="Stay out of the water entirely. Entering may also be an offence here.",
        ansi="\033[1;97;41m",
        hex_color="#8B0000",
        emoji="\N{LARGE RED SQUARE}\N{LARGE RED SQUARE}",
    ),
}


class Advisory(IntEnum):
    """Flags flown alongside the hazard flag, not instead of it."""

    PURPLE = 0
    NO_LIFEGUARD = 1
    WATER_QUALITY = 2
    UV_EXTREME = 3
    COLD_WATER = 4
    OFFSHORE_WIND = 5
    LIGHTNING = 6


@dataclass(frozen=True)
class AdvisoryInfo:
    advisory: Advisory
    label: str
    meaning: str
    hex_color: str
    emoji: str


ADVISORY_INFO: dict[Advisory, AdvisoryInfo] = {
    Advisory.PURPLE: AdvisoryInfo(
        Advisory.PURPLE,
        "PURPLE",
        "Dangerous marine life may be present",
        "#7B2FBE",
        "\N{LARGE PURPLE SQUARE}",
    ),
    Advisory.NO_LIFEGUARD: AdvisoryInfo(
        Advisory.NO_LIFEGUARD,
        "NO LIFEGUARD",
        "Outside typical patrol hours - nobody is watching the water",
        "#5A6473",
        "\N{LARGE BLUE SQUARE}",
    ),
    Advisory.WATER_QUALITY: AdvisoryInfo(
        Advisory.WATER_QUALITY,
        "WATER QUALITY",
        "Recent rainfall makes a bacterial advisory likely",
        "#8A6F3D",
        "\N{LARGE BROWN SQUARE}",
    ),
    Advisory.UV_EXTREME: AdvisoryInfo(
        Advisory.UV_EXTREME,
        "UV",
        "Very high or extreme UV - burn time is minutes, not hours",
        "#E0611A",
        "\N{LARGE ORANGE SQUARE}",
    ),
    Advisory.COLD_WATER: AdvisoryInfo(
        Advisory.COLD_WATER,
        "COLD WATER",
        "Cold shock risk on entry regardless of how warm the air is",
        "#2E7FCB",
        "\N{LARGE BLUE SQUARE}",
    ),
    Advisory.OFFSHORE_WIND: AdvisoryInfo(
        Advisory.OFFSHORE_WIND,
        "OFFSHORE WIND",
        "Wind is blowing off the beach - inflatables and boards will be carried out",
        "#4B9E8F",
        "\N{LARGE BLUE SQUARE}",
    ),
    Advisory.LIGHTNING: AdvisoryInfo(
        Advisory.LIGHTNING,
        "LIGHTNING",
        "Thunderstorms in the area - beaches clear the water for these",
        "#B08CE0",
        "\N{HIGH VOLTAGE SIGN}",
    ),
}

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
