# Diffy

**Local AI coding assistant powered by git-diff RAG, semantic search, and Ollama.**

Diffy indexes your repository's commit diffs and live workspace files, builds a hybrid TF-IDF + semantic vector store, and uses a local (or cloud) LLM for deep, context-aware code assistance — all running privately on your machine.

## Features

- **Chat Sidebar** — Persistent chat panel in the VS Code Activity Bar with streaming responses and a loading indicator
- **Git-Diff RAG** — Retrieves relevant code changes from your commit history to answer questions
- **Hybrid Semantic Search** — Combines TF-IDF keyword matching with Ollama embedding cosine similarity for accurate retrieval
- **"Ask Diffy" CodeLens** — Click-to-ask links above every function and class definition
- **Live Workspace Indexing** — Automatically indexes files when you save (`Ctrl+S`), keeping the index in sync with your editor
- **Git Modification Highlights** — Gutter icons and subtle highlights on lines recently changed in git history
- **Native Diff Viewer** — Click RAG context chips to open VS Code's diff viewer showing exactly what changed in that commit
- **AI Code Review** — Run an AI review of your last N commits with full Markdown output
- **Event-driven auto-indexing** — Detects new commits via VS Code Git API, git hooks, and GitHub webhooks
- **Multi-provider LLM** — Uses Ollama locally, or set an API key for OpenAI, Anthropic, or Gemini
- **Zero mandatory pip dependencies** — Core Python server uses only the standard library; `python-dotenv` and `mcp` are optional extras

## Prerequisites

1. **Python 3.10+** — [python.org](https://python.org)
2. **Node.js 18+** — [nodejs.org](https://nodejs.org)
3. **Ollama** — [ollama.com](https://ollama.com)
4. **A code model** (for generation):
   ```bash
   ollama pull codellama
   # or a lighter alternative
   ollama pull qwen2.5-coder:7b
   ```
5. **An embedding model** (for semantic search):
   ```bash
   ollama pull nomic-embed-text
   ```

## Local Setup & Development

### 1. Clone and install dependencies

```bash
git clone https://github.com/yourname/diffy.git
cd diffy

# Install Node.js dependencies
npm install

# Compile TypeScript
npm run compile
```

### 2. Set up the Python virtual environment

```bash
# Create a venv inside the extension directory
python -m venv .venv

# Activate it
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# Optional: install python-dotenv for .env file support
pip install python-dotenv

# Optional: install mcp for MCP server support
pip install mcp

# Or install everything at once from the server directory
# pip install -e "server/[all]"
```

> **Note:** No pip packages are strictly required. The core server works with Python's standard library alone. `python-dotenv` unlocks `.env` file loading; `mcp` enables the optional MCP server.

### 3. Configure environment (optional)

Copy the example env file and fill in any values you need:

```bash
cp .env.example server/.env
```

Key variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `DIFFY_OLLAMA_URL` | `http://localhost:11434` | Ollama API URL |
| `DIFFY_MODEL` | `codellama` | LLM model name |
| `DIFFY_EMBED_MODEL` | `nomic-embed-text` | Embedding model name |
| `DIFFY_OPENAI_API_KEY` | _(empty)_ | Enables OpenAI instead of Ollama |
| `DIFFY_ANTHROPIC_API_KEY` | _(empty)_ | Enables Anthropic Claude |
| `DIFFY_GEMINI_API_KEY` | _(empty)_ | Enables Google Gemini |
| `DIFFY_GITHUB_TOKEN` | _(empty)_ | For GitHub webhook diffs |

### 4. Point the extension to your Python venv

In VS Code settings (`Ctrl+,`), set:

```json
"diffy.pythonPath": ".venv/Scripts/python.exe"
```

On macOS/Linux:
```json
"diffy.pythonPath": ".venv/bin/python"
```

### 5. Launch the Extension Development Host

```
Press F5 in VS Code
```

A new VS Code window opens with Diffy active. The rocket icon `🚀 Diffy ✓` will appear in the status bar when the server connects successfully.

---

## Usage

### Chat Sidebar

Click the **rocket icon** in the Activity Bar to open the Diffy chat panel. Type a question and press **Ask** or `Enter`. Responses stream in real time.

**Keyboard shortcut:** `Enter` to submit, `Shift+Enter` for a new line.

### Ask Diffy CodeLens

Open any source file — a `$(rocket) Ask Diffy` link appears above every function and class definition. Clicking it auto-populates the chat with a question about that symbol.

### Context Chips

After each AI response, **context chips** appear showing which commits and files were used as RAG context. Click a chip to open VS Code's native diff viewer showing the exact changes in that commit.

### Commands (`Ctrl+Shift+P`)

| Command | Description |
|---|---|
| `Diffy: Ask a Question` | Focus the chat sidebar |
| `Diffy: Index Current Repository` | Manually re-index the current repo |
| `Diffy: Review Recent Commits` | AI code review of the last N commits (Markdown output) |
| `Diffy: Show Status` | View indexing stats, LLM provider, and system health |
| `Diffy: Select Model` | Choose an Ollama model |
| `Diffy: Configure` | Set Ollama URL, webhook port, max commits, etc. |
| `Diffy: Clear Index` | Wipe all indexed data |

### Auto-Indexing

Diffy automatically keeps its index up to date via:

1. **On file save** — Any file you save is immediately chunked and indexed into the vector store
2. **VS Code Git API** — Detects local commits, checkouts, merges, and pulls
3. **Git hooks** — `post-commit` / `post-merge` hooks as a fallback
4. **GitHub Webhooks** — Optional: receives push events for remote changes

### GitHub Webhooks (Optional)

To receive push notifications from GitHub:

1. Run `Diffy: Configure` → set **GitHub Token** and **Webhook Port**
2. Expose your local port (e.g., [ngrok](https://ngrok.com) or [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)):
   ```bash
   ngrok http 9417
   ```
3. In your GitHub repo → **Settings → Webhooks → Add webhook**:
   - Payload URL: `https://your-tunnel.example.com/webhook`
   - Content type: `application/json`
   - Secret: _(match your Diffy config)_
   - Events: `push`

---

## Configuration

All settings are available under `Diffy` in VS Code Settings (`Ctrl+,`):

| Setting | Default | Description |
|---|---|---|
| `diffy.ollamaUrl` | `http://localhost:11434` | Ollama API endpoint |
| `diffy.model` | `codellama` | LLM model for generation |
| `diffy.embedModel` | `nomic-embed-text` | Ollama model for embeddings |
| `diffy.webhookPort` | `9417` | Webhook receiver port |
| `diffy.maxCommits` | `200` | Max commits to index per repo |
| `diffy.topK` | `5` | Diff chunks retrieved per query |
| `diffy.pythonPath` | `.venv/Scripts/python.exe` | Path to Python interpreter |
| `diffy.autoIndex` | `true` | Auto-index on commit detection |

---

## Architecture

```
VS Code Extension (TypeScript)
├── extension.ts              Entry point, command registration, auto-index
├── serverClient.ts          Python process manager (JSON-RPC over stdin/stdout)
├── commitDetector.ts         Event-driven commit detection (Git API + hooks)
├── providers/
│   ├── chatViewProvider.ts   Streaming chat sidebar with loading state
│   ├── diffyCodeLensProvider.ts  "Ask Diffy" links above functions & classes
│   └── diffyDecorationProvider.ts  Git modification gutter highlights (debounced)
└── commands/
    ├── showDiff.ts           Native VS Code diff viewer (diffy-git: URI scheme)
    └── reviewCommit.ts       AI code review panel with Markdown rendering

Python Server (stdlib + optional extras)
├── main.py                   JSON-RPC dispatcher (stdin/stdout)
├── rag_pipeline.py           RAG orchestrator: index, retrieve, query, review
├── vectorstore.py            Hybrid TF-IDF + cosine similarity (SQLite)
├── git_integration.py        Local git (subprocess) + GitHub REST API
├── llm_client.py             Unified LLM: Ollama / OpenAI / Anthropic / Gemini
├── ollama_client.py          Ollama HTTP client with streaming + embeddings
├── webhook_server.py         GitHub webhook receiver (HTTP server)
├── mcp_server.py             Optional MCP server (query / retrieve / review_commits)
└── config.py                 Config: env vars, .env file, config.json
```

### How RAG Works

1. **Indexing** — Each commit diff is chunked per-file. Each chunk is embedded via Ollama (`nomic-embed-text`) and stored in SQLite alongside its TF-IDF terms.
2. **Retrieval** — At query time, the question is embedded. Candidates are fetched via the SQLite inverted index (fast), then re-ranked using a hybrid score: `0.3 × TF-IDF + 0.7 × cosine similarity`.
3. **Generation** — Top-K chunks are injected into the LLM prompt as context. The LLM response streams back token-by-token to the chat sidebar.

---

## Troubleshooting

**`🚀 Diffy ✗` in the status bar** — The Python server failed to start.
- Check the **Diffy Server** output channel for details.
- Make sure `diffy.pythonPath` points to your `.venv` Python.
- Verify `python-dotenv` is installed: `.venv/Scripts/pip install python-dotenv`

**No results returned from queries** — The index may be empty.
- Run `Diffy: Index Current Repository` manually.
- Ensure Ollama is running: `ollama serve`
- Ensure the embedding model is pulled: `ollama pull nomic-embed-text`

**Decorations not appearing** — The file may be untracked or git blame failed.
- The provider only activates after a file save.
- Untracked (new) files have no git history — this is expected.

---

## MCP Server

Diffy ships an optional [Model Context Protocol](https://modelcontextprotocol.io/) server (`server/mcp_server.py`) that exposes its RAG index as MCP tools. This lets AI assistants like **Claude Desktop**, **Cursor**, and **Claude Code** query your codebase directly.

### Tools exposed

| Tool | Description |
|---|---|
| `query(question)` | Ask a natural-language question; returns an LLM answer grounded in your diff history |
| `retrieve(question, top_k)` | Return the top-k raw diff chunks (JSON) without calling the LLM |
| `review_commits(repo_path, num_commits)` | Run a full AI code review over the last N commits; returns Markdown |

### Installation

The MCP server is an **optional** component — the VS Code extension does not require it. Install only what you need:

```bash
# MCP server only
pip install "diffy-backend[mcp]"

# python-dotenv only (.env file support)
pip install "diffy-backend[dotenv]"

# Everything
pip install "diffy-backend[all]"
```

Or install from source:

```bash
cd server
pip install -e ".[mcp]"
```

### Connecting Claude Desktop

Add the following to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "diffy": {
      "command": "python",
      "args": ["/absolute/path/to/diffy/server/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop. The `query`, `retrieve`, and `review_commits` tools will appear automatically.

### Connecting Cursor

In Cursor settings → **MCP**, add a new server:

```json
{
  "name": "diffy",
  "command": "python",
  "args": ["/absolute/path/to/diffy/server/mcp_server.py"]
}
```

### Connecting Claude Code (CLI)

```bash
claude mcp add diffy python /absolute/path/to/diffy/server/mcp_server.py
```

---

## License

MIT
