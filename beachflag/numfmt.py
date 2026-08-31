"""One rounding convention, shared by the Python and JavaScript front ends.

Python's format spec rounds an exact tie to even (`f"{18.5:.0f}"` is "18");
JavaScript's `toFixed` rounds it away from zero ("19"). Neither is wrong, but a
beach report that says 18 C in the terminal and 19 C in the browser for the same
forecast is a bug, and the parity test between the two implementations is what
surfaced it.

Away-from-zero is also the convention a reader expects, so that is the one both
sides use. Quantising the exact binary value - not `repr` of it - is what keeps
this identical to `toFixed`: a value that only looks like a tie in decimal
(0.145 is really 0.14499...) has to round down on both sides.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def fmt(value: float, places: int = 0) -> str:
    """Format like JavaScript's Number.prototype.toFixed."""
    quantum = Decimal(1).scaleb(-places)
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def signed(value: float, places: int = 0) -> str:
    """As `fmt`, but always carrying an explicit + or -."""
    text = fmt(value, places)
    return text if text.startswith("-") else f"+{text}"
