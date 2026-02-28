# Diffy

**Local coding assistant powered by git-diff RAG and Ollama.**

Diffy indexes your repository's commit diffs, builds an in-house TF-IDF vector store, and uses a local LLM for context-aware code assistance — all running on your machine.

## Features

- **Git-Diff RAG** — Retrieves relevant code changes from your commit history to answer questions
- **Event-driven indexing** — Automatically detects new commits via VS Code Git API, git hooks, and GitHub webhooks
- **Local LLM** — Uses Ollama for private, on-device inference
- **Zero pip dependencies** — Python backend uses only the standard library
- **In-house vector store** — Custom TF-IDF with cosine similarity, no external libraries

## Prerequisites

1. **Python 3.10+** — [python.org](https://python.org)
2. **Node.js 18+** — [nodejs.org](https://nodejs.org)
3. **Ollama** — [ollama.com](https://ollama.com)
4. **A code model**:
   ```bash
   ollama pull codellama
   # or
   ollama pull deepseek-coder
   ```

## Setup

```bash
# Clone / navigate to the extension directory
cd diffy

# Install Node dependencies
npm install

# Compile TypeScript
npm run compile
```

## Usage

### In VS Code

1. Open the extension folder in VS Code
2. Press **F5** to launch the Extension Development Host
3. Open any git repository in the new window

### Commands (Ctrl+Shift+P)

| Command | Description |
|---|---|
| `Diffy: Ask a Question` | Ask about your codebase using RAG context |
| `Diffy: Index Current Repository` | Manually index the current repo |
| `Diffy: Show Status` | View indexing status and system health |
| `Diffy: Select Model` | Choose an Ollama model |
| `Diffy: Configure` | Set Ollama URL, GitHub token, etc. |
| `Diffy: Clear Index` | Clear all indexed data |

### Auto-Indexing

Diffy automatically detects and indexes new commits via:

1. **VS Code Git API** — Detects local commits, checkouts, merges, pulls
2. **Git hooks** — Installs post-commit/post-merge hooks as a backup
3. **GitHub Webhooks** — Receives push events for remote changes (optional)

### GitHub Webhooks (Optional)

To receive push notifications from GitHub:

1. Run `Diffy: Configure` → Set **GitHub Token** and **Webhook Port**
2. Expose your local port (e.g., via [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) or [ngrok](https://ngrok.com))
3. In your GitHub repo → Settings → Webhooks → Add:
   - Payload URL: `https://your-tunnel.example.com/webhook`
   - Content type: `application/json`
   - Secret: (match your Diffy config)
   - Events: `push`

## Configuration

| Setting | Default | Description |
|---|---|---|
| `diffy.ollamaUrl` | `http://localhost:11434` | Ollama API endpoint |
| `diffy.model` | `codellama` | Default LLM model |
| `diffy.webhookPort` | `9417` | Webhook receiver port |
| `diffy.maxCommits` | `200` | Max commits to index per repo |
| `diffy.topK` | `5` | Diff chunks retrieved per query |
| `diffy.pythonPath` | `python` | Python interpreter path |
| `diffy.autoIndex` | `true` | Auto-index on commit detection |

## Architecture

```
VS Code Extension (TypeScript)
├── extension.ts          Entry point, commands
├── backendClient.ts      Python process manager (JSON-RPC)
├── commitDetector.ts     Event-driven commit detection
│
Python Backend (stdlib only)
├── main.py               JSON-RPC server (stdin/stdout)
├── git_integration.py    Local git + GitHub API
├── vectorstore.py        TF-IDF + cosine similarity
├── rag_pipeline.py       RAG orchestrator
├── ollama_client.py      Ollama HTTP client
├── webhook_server.py     GitHub webhook receiver
└── config.py             Configuration
```

## License

MIT
