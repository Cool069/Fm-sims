import re


def parse_amount(raw: str) -> int:
    """
    Parse a human-friendly amount string into an integer.

    Supports:
        100k  -> 100,000
        1.5m  -> 1,500,000
        2.3b  -> 2,300,000,000
        1e6   -> 1,000,000
        1.5e3 -> 1,500
        500   -> 500
    Case-insensitive. Raises ValueError on bad input.
    """
    raw = raw.strip().lower().replace(",", "")

    # Scientific notation: 1e6, 2.5e3, etc.
    sci = re.fullmatch(r"(\d+(?:\.\d+)?)e(\d+)", raw)
    if sci:
        base     = float(sci.group(1))
        exponent = int(sci.group(2))
        return int(base * (10 ** exponent))

    # Suffix notation: k, m, b
    suffix = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb])", raw)
    if suffix:
        base = float(suffix.group(1))
        s    = suffix.group(2)
        multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
        return int(base * multipliers[s])

    # Plain integer
    plain = re.fullmatch(r"\d+", raw)
    if plain:
        return int(raw)

    raise ValueError(f"Cannot parse amount: {raw!r}")


def fmt(amount: int) -> str:
    """Format an integer balance, handling negatives: -£500,000"""
    if amount < 0:
        return f"-£{abs(amount):,}"
    return f"£{amount:,}"