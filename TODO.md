# Diffy — TODO: Chokepoints & Vulnerabilities

---

## 🔴 Critical — Security Vulnerabilities

### 1. [ ] Unvalidated `repo_path` in git commands
**File:** `backend/git_integration.py` (L23–38)

`_run_git` passes user-controlled `repo_path` directly into `subprocess.run`. Could allow path traversal or operations on unintended repositories.

**Fix:** Validate `repo_path` is an absolute path to a directory containing `.git` before executing any git commands.

---

### 2. [ ] Webhook HMAC signature bypass (default config)
**Files:** `backend/webhook_server.py` (L32–35) · `backend/config.py` (L15)

`webhook_secret` defaults to `""`, causing `_verify_signature` to return `True` unconditionally — any HTTP client can POST forged payloads and poison the RAG index (prompt injection vector).

**Fix:** Require a secret before the webhook starts, or add a nonce-based auth layer.

---

### 3. [ ] GitHub token stored in plaintext
**File:** `backend/config.py` (L67–72)

`github_token` is written to `~/.diffy/config.json` with no encryption or file permission hardening. Also passed as an environment variable in `backendClient.ts` (L80–88).

**Fix:** Use VS Code's `SecretStorage` API. Avoid passing secrets as env vars.

---

### 4. [ ] `setConfig` allows arbitrary key injection / SSRF
**File:** `backend/main.py` (L156–159)

`handle_set_config` calls `cfg.update(params)` with no validation. An attacker can set `ollama_url` to a remote server, turning Diffy into an exfiltration proxy for user queries and code diffs.

**Fix:** Whitelist allowed config keys. Validate values (e.g., `ollama_url` must be localhost).

---

## 🟠 High — Performance Bottlenecks

### 5. [ ] O(n) linear scan on every search query
**File:** `backend/vectorstore.py` (L242–272)

Every query computes cosine similarity against **all** stored vectors. At 5,000+ docs, expect 200–500ms per query.

**Fix:** Implement an inverted index to pre-filter candidates, or use approximate nearest neighbor (ANN).

---

### 6. [ ] Full TF-IDF rebuild on every index operation
**File:** `backend/vectorstore.py` (L225–240)

`rebuild()` re-fits the entire vocabulary and re-encodes all documents from scratch on each add.

**Fix:** Implement incremental TF-IDF updates — track DF deltas, extend vocabulary, re-vectorize only affected docs.

---

### 7. [ ] Single monolithic JSON persistence
**File:** `backend/vectorstore.py` (L300–315)

Every save serializes all documents + vectors + vocabulary into one JSON file. Causes write amplification, memory doubling, and corruption risk on crash.

**Fix:** Use SQLite with WAL mode, or append-only log files with periodic compaction.

---

## 🟡 Medium — Concurrency & Architecture

### 8. [ ] Thread-unsafe shared state
**Files:** `backend/main.py` (L294–299) · `backend/vectorstore.py`

Each JSON-RPC request spawns a thread, but `_pipeline`, vector store, and config have no locks. Concurrent index + query causes dict mutation during iteration and file write races.

**Fix:** Add `threading.Lock` guards around VectorStore mutations and RAG pipeline state.

---

### 9. [ ] Notification handler overwrite race
**Files:** `src/extension.ts` (L130–151) · `src/commitDetector.ts` (L226–241)

`BackendClient` supports only one notification handler. `cmdAskQuestion` and `CommitDetector` overwrite each other — webhook events are silently dropped during streaming.

**Fix:** Use an event emitter / pub-sub pattern allowing multiple subscribers.

---

### 10. [ ] No JSON-RPC parameter validation
**File:** `backend/main.py` (L192–216)

No type/bounds checks on `top_k`, `question`, `repoPath`, etc. Allows memory exhaustion via oversized strings or massive `top_k`.

**Fix:** Add schema validation with type checks and bounds for each handler.

---

### 11. [ ] Silent error swallowing
**Files:** `backend/config.py` (L37–38) · `backend/rag_pipeline.py`

Multiple `except: pass` blocks silently discard errors. Corrupted config or vector store produces zero diagnostics.

**Fix:** Log errors to stderr at minimum. Surface warnings to the user.

---

### 12. [ ] Git hook modification without user consent
**File:** `src/commitDetector.ts` (L175–200)

Extension writes/appends to `post-commit`, `post-merge`, `post-checkout` hooks silently. Can break user's CI/CD hooks. No cleanup on uninstall.

**Fix:** Prompt user before installing hooks. Add a cleanup/uninstall mechanism.

---

## 🔵 Low — Minor Issues

### 13. [ ] No webhook rate limiting
**File:** `backend/webhook_server.py`

Flood of POST requests spawns unlimited threads hitting GitHub API.

### 14. [ ] Doc ID hash collision risk
**File:** `backend/vectorstore.py` (L354–357)

SHA-256 truncated to 16 hex chars (64 bits) — collision at ~65K docs (birthday paradox).

### 15. [ ] No Python dependency manifest
No `requirements.txt` or `pyproject.toml` for reproducible installs.
