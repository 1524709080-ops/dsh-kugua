# dsh-bridge

Let any **MCP client** (codex, Claude Code, Cursor, Windsurf, Cline …) read conversations
from **DeepSeek Harness (DSH)**; and provide a CLI tool to read **codex** conversations.

> At its core is a standard stdio MCP server — not tied to any specific client. If a client
> supports MCP, it can connect.

---

## What each script does

| File | Purpose | Direction | Dependencies |
|---|---|---|---|
| `dsh_mcp_server.py` | MCP server exposing 3 tools to any client | client → read DSH | `mcp`, `zstandard` |
| `dsh_bridge.py` | CLI tool to read DSH conversations directly | you → read DSH | `zstandard` |
| `codex_bridge.py` | CLI tool to read codex conversations | DSH / you → read codex | `zstandard` (optional) |

The 3 exposed MCP tools:

- `list_dsh_sessions` — list all DSH sessions (index / title / id / cwd / time)
- `dump_dsh_session(target, show_reasoning)` — export a session's full conversation
- `tail_dsh_session(target, n, show_reasoning)` — show the last N messages of a session

---

## Compatibility

### Supported MCP clients

Any client implementing the **MCP stdio transport** works, including (but not limited to):

- **codex** (desktop / CLI)
- **Claude Code**
- **Cursor**
- **Windsurf**
- **WorkBuddy** (Tencent)
- **Trae / 豆包编程 (MarsCode)** (ByteDance)
- **Cline / Roo Code / Continue**

### Platforms

- **Windows / macOS / Linux** all supported. The scripts are pure Python relying only on the
  standard library plus `mcp` and `zstandard`, with no platform-specific code; `install.py`
  handles path separators and executable-name differences automatically.

### Prerequisites

- **Python 3.8+** (3.10+ recommended).
- DSH **does not need to be running** — the scripts read session files directly from disk.
- codex **does not need to be running** — `codex_bridge.py` reads its local storage directly.

### Version compatibility

| Target | Format read | Notes |
|---|---|---|
| DSH sessions | `session.v4.jsonl.zstd` (v4) | Unknown event types are skipped (not fatal), so it is forward-compatible |
| codex sessions | `state_5.sqlite` + `rollout-*.jsonl(.zst)` + `thread_history_1.sqlite` | Old sessions (`.zst`) are decompressed |

If upstream (DSH / codex) changes its storage format in a future release, the parsing logic
may need a small update. Unknown fields are accessed defensively via `get()`, so a single
malformed entry will not crash the tool.

### Limitations

- **Read-only**: never writes, modifies, or deletes any conversation.
- **Not real-time**: each invocation reads a disk snapshot; there is no continuous push.

---

## Quick start (3 steps)

### Step 1: Install dependencies

```bash
python install.py
```

It auto-detects a usable python, installs `mcp` + `zstandard`, and prints configs for each
client. (On a slow network, add `--mirror https://pypi.tuna.tsinghua.edu.cn/simple`.)

Or install manually:

```bash
pip install mcp zstandard
```

### Step 2: Configure your client

Paste the config printed by `install.py` into the target client (`command` and `args` are
already absolute paths). Or run `python install.py --codex` to write the codex config directly.

### Step 3: Restart the client

After restarting, the client can call `list_dsh_sessions` / `dump_dsh_session` /
`tail_dsh_session`.

---

## Per-client MCP config

### codex (desktop)

Append to `~/.codex/config.toml`:

```toml
[mcp_servers.dsh_bridge]
command = '/path/to/python'
args = ['/absolute/path/dsh_mcp_server.py']
startup_timeout_sec = 60
```

### Claude Code

```bash
claude mcp add dsh-bridge -- "/path/to/python" "/absolute/path/dsh_mcp_server.py"
```

### Cursor / Windsurf / others (generic JSON)

```json
{
  "mcpServers": {
    "dsh_bridge": {
      "command": "/path/to/python",
      "args": ["/absolute/path/dsh_mcp_server.py"]
    }
  }
}
```

### WorkBuddy (Tencent)

Write to `~/.workbuddy/mcp.json` (user-level, reused across projects), or a project-local
`.workbuddy/mcp.json`:

```json
{
  "mcpServers": {
    "dsh_bridge": {
      "command": "/path/to/python",
      "args": ["/absolute/path/dsh_mcp_server.py"]
    }
  }
}
```

### Trae / 豆包编程 (MarsCode)

Write to `.trae/mcp.json` (project-level) or the global `mcp.json`, in the same standard
`mcpServers` JSON format.

> Note: Doubao's **chat app / web version** does not support custom MCP servers — only the
> **Trae / 豆包编程 IDE** does.

> **Windows note**: in a TOML single-quoted string, backslashes must be escaped to `\\`
> (`install.py` does this for you). Also, the `python` command on Windows may be a
> Microsoft Store stub that exits immediately — use a real python path or `py -3`.

---

## CLI usage (without MCP)

```bash
# Read DSH sessions
python dsh_bridge.py list              # list all sessions
python dsh_bridge.py dump 0            # export session 0 in full
python dsh_bridge.py tail 0 10         # last 10 messages of session 0

# Read codex sessions (reverse direction)
python codex_bridge.py list            # list all codex sessions
python codex_bridge.py dump 0          # export session 0
python codex_bridge.py latest 5        # 5 most recent sessions
```

The `target` argument of `dump` / `tail` can be an index, a session-id prefix, or a title keyword.

---

## How it works: where the data lives

### DSH sessions

```
~/.dsh/sessions/<encoded-cwd>/<session-id>/session.v4.jsonl.zstd
```

- A **zstd stream-compressed** JSONL: one event per line, distinguished by the `type` field
  (`user/message`, `assistant/message`, `tool/call`, `tool/result`, `session/title`, …).
- Timestamps are standard Unix milliseconds.
- Decompress with `zstandard`'s frame-spanning `stream_reader` (plain `decompress()` fails
  because there is no content size).

### codex sessions

```
~/.codex/state_5.sqlite                       # session metadata (threads table)
~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl(.zst)   # per-event (authoritative; old = .zst)
~/.codex/thread_history_1.sqlite              # legacy fallback (thread_items table)
```

- `rollout-*.jsonl` is the authoritative, append-only source; old sessions are compressed
  to `.jsonl.zst`.
- Parsing filters out codex's environment context and developer system messages, keeping
  only the real conversation.

---

## FAQ

**Q: Still can't connect after setup?**
First confirm the server runs standalone: execute `python dsh_mcp_server.py` and look for the
JSON-RPC init response (`serverInfo.name = "dsh-bridge"`). If it appears, the server is fine
and the issue is in the client config.

**Q: `python` does nothing on Windows?**
That's the Microsoft Store stub. Use `py -3` or the full path to a real python.

**Q: Can it read the reasoning?**
Yes. The `show_reasoning` parameter of `dump_dsh_session` / `tail_dsh_session` controls it.

**Q: Only DSH and codex?**
The DSH side is a standard MCP server, so any MCP-capable client can connect. The codex side
(`codex_bridge.py`) reads its local storage and does not require codex to be running.

---

## Directory

```
dsh-bridge/
├── README.md            This document (中文)
├── README_EN.md         English version
├── install.py           One-shot installer (detect python + install deps + generate configs)
├── dsh_mcp_server.py    MCP server (core)
├── dsh_bridge.py        CLI to read DSH
└── codex_bridge.py      CLI to read codex (reverse, optional)
```

## License

MIT
