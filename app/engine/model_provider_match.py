"""Keep a seat from being handed a model its chosen provider cannot run.

Agents are decoupled from any AI model/provider — an agent is just a name + a
strategy (see migration 0040). New agent versions store ``model = NULL`` and the
connector runs whatever model the seat's chosen provider defaults to. But agent
versions created *before* the decouple still carry a legacy ``model`` value (for
example ``gpt-5.4-mini``). If the turn payload forwards that legacy model to a
seat whose provider can't run it, the connector's CLI rejects it — e.g.
``claude --model gpt-5.4-mini`` returns a 404 and every turn falls back to HOARD.

So the payload only forwards a model that clearly belongs to the seat's chosen
provider; otherwise it sends ``None`` and the connector picks that provider's own
default model.
"""

from __future__ import annotations

from app.config import PROVIDER_MODELS, provider_for_model


def default_model_for_provider(provider: str | None) -> str | None:
    """The server's default model for a provider, or ``None`` if it has none.

    The default is the first entry of the provider's ``PROVIDER_MODELS`` allowlist
    (the single source of truth). Providers with an empty allowlist — the MCP-only
    ones (``hermes``, ``openclaw``) — and unknown providers return ``None`` so no
    model is forced onto a CLI that has none.
    """
    if not provider:
        return None
    models = PROVIDER_MODELS.get(provider.lower(), [])
    return models[0] if models else None


def resolve_seat_model(provider: str | None, preferred_model: str | None) -> str | None:
    """Resolve the model to send for a machine-connection seat, in three layers:

    1. the agent's ``preferred_model`` if it belongs to the seat's chosen provider
       (kept by :func:`model_for_provider`); else
    2. the server's per-provider default (:func:`default_model_for_provider`); else
    3. ``None`` — the connector falls back to its own built-in default.

    A provider-mismatched or unset preferred model falls through quietly to the
    default; the legacy ``AgentVersion.model`` is no longer consulted.
    """
    default = default_model_for_provider(provider)
    if default is None:
        # The provider has no model allowlist (the MCP-only hermes/openclaw) or is
        # unknown/unset — forward no model at all, even if a stray preferred model
        # is present, since these CLIs take no ``--model``.
        return None
    kept = model_for_provider(provider, preferred_model)
    return kept if kept is not None else default


def model_for_provider(provider: str | None, model: str | None) -> str | None:
    """The model to put in a seat's turn payload, or ``None`` to let it default.

    Drop the model only when it *provably* belongs to a different provider than
    the seat's chosen one — that is the broken case (a legacy ``gpt-*`` model on a
    Claude seat 404s the claude CLI). ``provider_for_model`` (backed by
    ``PROVIDER_MODELS``, the single source of truth for model→provider) returns
    None for a name in no allowlist, so an unrecognized-but-plausible model is
    passed through unchanged rather than second-guessed. When the model is
    dropped the connector uses the provider's own default model.
    """
    if not model:
        return None
    implied = provider_for_model(model)
    if provider and implied is not None and implied != provider.lower():
        return None
    return model
