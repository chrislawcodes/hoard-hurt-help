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

from app.config import provider_for_model


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
