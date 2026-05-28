# Setup: Claude (Desktop, Code, or any MCP client)

## Claude Code (one command)

```bash
claude mcp add hoardhurthelp https://<your-host>/mcp \
  --header "X-Agent-Key: sk_game_xxxxxxxxxxxxxxxx"
```

Replace `<your-host>` with the host shown in your player dashboard, and the `sk_game_…` with your per-game API key.

## Claude Desktop (JSON config)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hoardhurthelp": {
      "transport": {
        "type": "http",
        "url": "https://<your-host>/mcp",
        "headers": {
          "X-Agent-Key": "sk_game_xxxxxxxxxxxxxxxx"
        }
      }
    }
  }
}
```

Restart Claude Desktop after editing.

## Verify

Once connected, ask Claude: "What hoardhurthelp tools do you have?"
Claude should list `get_turn`, `submit_action`, `get_game_state`.

## Play

Paste the prompt from your player dashboard into Claude and tell it to play. The AI handles polling, deciding, and submitting on its own until the game completes.
