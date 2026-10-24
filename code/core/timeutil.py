"""UTC timestamp parsing and formatting.

All evidence timestamps are ISO-8601 with a trailing ``Z``. Every comparison in the
system goes through aware ``datetime`` objects so that string ordering quirks cannot
leak into cutoff decisions.
"""

from datetime import datetime, timezone

ISO_PATTERN = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


def parse_ts(value):
    """Parse ``YYYY-MM-DDTHH:MM:SSZ`` into an aware UTC datetime; ``None`` passes through."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fmt_ts(value):
    """Format an aware datetime back to the dataset's ``...Z`` form."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
