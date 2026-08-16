"""Shared Jinja2Templates instance with custom filters.

All route modules import `templates` from here so filters are registered
once in a single place.
"""

from datetime import datetime, timezone

from fastapi.templating import Jinja2Templates
from jinja2 import Undefined
from jinja2.exceptions import UndefinedError
from markupsafe import Markup
from starlette.requests import Request

from app.aware_datetime import ensure_aware

# Re-exported for the Jinja filter below; the canonical definition lives in
# app.read_models.agent_display, which owns agent display-name formatting.
from app.games.hoard_hurt_help.rules import DEFAULT_MUTUAL_HELP_MODE, mutual_help_legend
from app.read_models.agent_display import strip_archive_suffix


def _nav_cta_context(request: Request) -> dict[str, object]:
    """Expose the smart Play CTA to every page.

    Populated by the ``populate_nav_cta`` router dependency on human-page
    routers; absent on API/fragment responses (where the nav isn't rendered),
    in which case the template simply omits the button.
    """
    return {
        "nav_cta": getattr(request.state, "nav_cta", None),
        "connection_count": getattr(request.state, "connection_count", 0),
        "live_connection_count": getattr(request.state, "live_connection_count", 0),
    }


templates = Jinja2Templates(
    directory="app/templates", context_processors=[_nav_cta_context]
)

# The replay legend must state THIS match's mutual-help payout. Exposing the
# helper as a global lets the match page build it from the match row it already
# has, instead of every route that renders a replay passing the string down.
templates.env.globals["mutual_help_legend"] = mutual_help_legend

# The rule a new match gets by default, so the create form pre-selects the same
# mode every other create path would have used.
templates.env.globals["default_mutual_help_mode"] = DEFAULT_MUTUAL_HELP_MODE.value


def mandatory(value: object) -> object:
    """Raise if a template variable that must always be supplied is missing.

    Jinja's default ``Undefined`` renders a missing variable as a silent blank
    instead of erroring — fine for optional copy, but wrong for a value like
    the robot-circle mutual-help legend, where a forgotten context key used to
    fall back to a hardcoded (and increasingly wrong) guess. Pipe a must-have
    variable through this filter so a missing include-site wiring fails the
    render loudly instead of quietly lying about the payout.
    """
    if isinstance(value, Undefined):
        raise UndefinedError(
            "required template variable was not provided by its caller"
        )
    return value


templates.env.filters["mandatory"] = mandatory


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
    dt = ensure_aware(dt).astimezone(timezone.utc)
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
