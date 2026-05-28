# ChatGPT Custom GPT

`manifest.json` is a template — `{BASE_URL}` gets substituted with your Railway URL when published.

## Publish path (private link)

1. In ChatGPT, go to *Explore GPTs* → *Create*.
2. *Configure* → upload `manifest.json` (with `{BASE_URL}` replaced).
3. Under *Actions*, the OpenAPI URL is `{BASE_URL}/openapi.json`.
4. Set auth to *API Key* (header `X-Agent-Key`) and instructions per the manifest.
5. Publish as *Only people with a link*.
6. Share the link from each player's dashboard.

## How a player connects

1. Click the "Add Hoard-Hurt-Help GPT" link in their player dashboard.
2. ChatGPT prompts them for the API key — they paste their `sk_game_…`.
3. Tell the GPT: "Play this game until it finishes."
