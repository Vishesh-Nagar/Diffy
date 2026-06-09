# Diffy: Phase 2 Improvement Roadmap

Diffy's core security and high-impact performance bottlenecks have been successfully resolved. This phase focuses on quality-of-life improvements, stability, and minor structural updates.

This roadmap is prioritized based on the remaining items in the `TODO.md` assessment.

## 1. 🟡 Medium-Impact Improvements

### Git Hook Modification without User Consent
- **Issue:** `src/commitDetector.ts` silently writes or appends to local `.git/hooks` (`post-commit`, `post-merge`, `post-checkout`) to track changes, potentially breaking users' existing CI/CD or custom hook setups.
- **Action:** Prompt the user for permission via VS Code UI before modifying their local git hooks. Add an uninstall/cleanup mechanism to gracefully restore hooks.

### Silent Error Swallowing
- **Issue:** Multiple `except: pass` blocks (e.g., in `rag_pipeline.py` state loading and `config.py`) silently discard errors, making it difficult to debug corrupted configurations or vector stores.
- **Action:** Log these errors to `stderr` at minimum, and surface warnings to the user via the VS Code extension when critical state loading fails.

## 2. 🔵 Low-Impact Improvements

### Webhook Rate Limiting
- **Issue:** The GitHub webhook receiver (`backend/webhook_server.py`) spins up a new thread for every incoming POST request without any rate limiting, opening the door to thread starvation and memory exhaustion.
- **Action:** Implement a basic queue or thread-pool executor to bound the number of concurrent webhook processing threads.

### Doc ID Hash Collision Risk
- **Issue:** In `backend/vectorstore.py` (`make_doc_id`), the SHA-256 hash is truncated to 16 hex characters (64 bits), risking hash collisions at around ~65,000 documents due to the birthday paradox.
- **Action:** Increase the truncation length to 32 or 64 characters (128-256 bits) to practically eliminate the risk of collisions.
