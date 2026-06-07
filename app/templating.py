"""Shared Jinja2Templates instance with custom filters.

All route modules import `templates` from here so filters are registered
once in a single place.
"""

import re
from datetime import datetime, timezone

from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.requests import Request

_ARCHIVE_SUFFIX_RE = re.compile(
    r"\s+\(archived\s[^)]+\)(?:\s*#\d+)?$|\s+#\d+$"
)


def strip_archive_suffix(name: str) -> str:
    """Remove the archived suffix used in display labels."""
    return _ARCHIVE_SUFFIX_RE.sub("", name)


def _nav_cta_context(request: Request) -> dict[str, object]:
    """Expose the smart Play CTA to every page.

    Populated by the ``populate_nav_cta`` router dependency on human-page
    routers; absent on API/fragment responses (where the nav isn't rendered),
    in which case the template simply omits the button.
    """
    return {"nav_cta": getattr(request.state, "nav_cta", None)}


templates = Jinja2Templates(
    directory="app/templates", context_processors=[_nav_cta_context]
)


def _to_utc_iso(value: object) -> str | None:
    """Normalize a datetime or ISO string to a UTC ISO-8601 string with Z.

    We always store timestamps as UTC. SQLite may return them naive, so a
    naive value is assumed to be UTC (correct given our storage contract).
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return value  # unparseable — show as-is
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def localdt(value: object) -> Markup:
    """Render a timestamp as a <time> element the browser localizes.

    Outputs the UTC ISO value in the `datetime` attribute and as the text
    content; client-side JS (in base.html) rewrites the text to the viewer's
    local time. With JS off, the UTC value still shows.
    """
    iso = _to_utc_iso(value)
    if iso is None:
        return Markup("—")
    return Markup(f'<time class="localtime" datetime="{iso}">{iso}</time>')


templates.env.filters["localdt"] = localdt
templates.env.filters["strip_archive_suffix"] = strip_archive_suffix


def reltime(value: object) -> str:
    """Return a human-readable relative time string like 'in 42 min' or '5 min ago'."""
    iso = _to_utc_iso(value)
    if iso is None:
        return "unknown time"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    total_seconds = int((dt - now).total_seconds())
    if total_seconds < -3600:
        return f"{abs(total_seconds) // 3600}h ago"
    if total_seconds < -60:
        return f"{abs(total_seconds) // 60} min ago"
    if total_seconds < 60:
        return "starting now"
    if total_seconds < 3600:
        return f"in {total_seconds // 60} min"
    return f"in {total_seconds // 3600}h"

templates.env.filters["reltime"] = reltime
