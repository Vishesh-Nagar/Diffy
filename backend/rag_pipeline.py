"""
Diffy — RAG Pipeline
Orchestrates: index repository → retrieve relevant diffs → build prompt → call LLM.
Manages multiple indexed repositories with persistent state.
"""

import os
import json
import hashlib
import sys
import time

import config as cfg
import git_integration as git
import vectorstore as vs
import ollama_client as ollama


# ---------------------------------------------------------------------------
# Chunking: split diffs into indexable chunks
# ---------------------------------------------------------------------------

def _chunk_diff(commit_info, parsed_files, max_lines=None):
    """
    Split a commit's parsed diff into indexable chunks.
    Each chunk represents one file's changes in one commit.
    Returns: [{id, text, metadata}]
    """
    if max_lines is None:
        max_lines = cfg.get("chunk_max_lines", 80)

    repo_name = commit_info.get("repo_name", "unknown")
    commit_hash = commit_info["hash"]
    commit_msg = commit_info.get("message", "")
    commit_author = commit_info.get("author", "")
    commit_date = commit_info.get("date", "")

    chunks = []
    for file_info in parsed_files:
        file_path = file_info["file"]

        # Build readable text for this file's changes
        lines = []
        lines.append(f"Commit: {commit_hash[:8]} — {commit_msg}")
        lines.append(f"Author: {commit_author} | Date: {commit_date}")
        lines.append(f"File: {file_path}")
        lines.append("")

        for hunk in file_info["hunks"]:
            if hunk.get("context_label"):
                lines.append(f"Context: {hunk['context_label']}")

            for r in hunk["removed"]:
                lines.append(f"- {r}")
            for a in hunk["added"]:
                lines.append(f"+ {a}")

            # Truncate very large hunks
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                lines.append("... (truncated)")
                break

        text = "\n".join(lines)
        doc_id = vs.make_doc_id(repo_name, commit_hash, file_path)

        chunks.append({
            "id": doc_id,
            "text": text,
            "metadata": {
                "repo": repo_name,
                "commit": commit_hash,
                "short_hash": commit_info.get("short_hash", commit_hash[:7]),
                "message": commit_msg,
                "author": commit_author,
                "date": commit_date,
                "file": file_path,
            },
        })

    return chunks


# ---------------------------------------------------------------------------
# RAG Pipeline
# ---------------------------------------------------------------------------

class RAGPipeline:
    """
    Manages indexing and retrieval of git diffs for RAG-based code generation.
    """

    def __init__(self):
        self._store = vs.VectorStore()
        self._ollama = ollama.OllamaClient()
        self._repos = {}  # repo_path -> {name, last_indexed_hash, last_indexed_time}
        self._state_path = os.path.join(cfg.get("index_dir"), "pipeline_state.json")
        self._store_path = os.path.join(cfg.get("index_dir"), "vectorstore.json")
        self._load_state()

    # ----- State Management -----

    def _load_state(self):
        """Load pipeline state and vector store from disk."""
        # Load pipeline state
        if os.path.exists(self._state_path):
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._repos = state.get("repos", {})
            except (json.JSONDecodeError, OSError):
                pass

        # Load vector store
        self._store.load(self._store_path)

    def _save_state(self):
        """Save pipeline state and vector store to disk."""
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)

        state = {"repos": self._repos}
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)

        self._store.save(self._store_path)

    # ----- Indexing -----

    def index_repository(self, repo_path, force=False):
        """
        Index a local git repository's commit diffs.
        Returns: {status, commits_indexed, chunks_added}
        """
        repo_info = git.get_repo_info(repo_path)
        if not repo_info["branch"]:
            return {"status": "error", "message": "Not a git repository"}

        repo_name = repo_info["name"]
        abs_path = os.path.abspath(repo_path)

        # Check what we've already indexed
        repo_state = self._repos.get(abs_path, {})
        last_hash = repo_state.get("last_indexed_hash", "") if not force else ""

        # Get commits
        commits = git.get_commits(repo_path)
        if not commits:
            return {"status": "ok", "commits_indexed": 0, "chunks_added": 0}

        # Filter to only new commits (stop at last_indexed_hash)
        new_commits = []
        for c in commits:
            if c["hash"] == last_hash:
                break
            new_commits.append(c)

        if not new_commits:
            return {"status": "ok", "commits_indexed": 0, "chunks_added": 0}

        # Index each commit's diff
        total_chunks = 0
        all_items = []
        for commit in new_commits:
            raw_diff = git.get_diff(repo_path, commit["hash"])
            if not raw_diff:
                continue

            parsed = git.parse_diff(raw_diff)
            commit["repo_name"] = repo_name
            chunks = _chunk_diff(commit, parsed)

            for chunk in chunks:
                if not self._store.has_doc(chunk["id"]):
                    all_items.append(chunk)
                    total_chunks += 1

        if all_items:
            self._store.add_batch(all_items)
            self._store.rebuild()

        # Update repo state
        self._repos[abs_path] = {
            "name": repo_name,
            "last_indexed_hash": commits[0]["hash"],
            "last_indexed_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "remote": repo_info["remote"],
        }
        self._save_state()

        return {
            "status": "ok",
            "commits_indexed": len(new_commits),
            "chunks_added": total_chunks,
        }

    def index_diffs(self, diffs_data):
        """
        Index pre-fetched diffs (e.g., from GitHub webhook).
        diffs_data: {repo_name, commits: [{hash, message, author, date, raw_diff}]}
        """
        repo_name = diffs_data.get("repo_name", "unknown")
        all_items = []

        for commit in diffs_data.get("commits", []):
            raw_diff = commit.get("raw_diff", "")
            if not raw_diff:
                continue

            parsed = git.parse_diff(raw_diff)
            commit_info = {
                "hash": commit["hash"],
                "short_hash": commit.get("hash", "")[:7],
                "message": commit.get("message", ""),
                "author": commit.get("author", ""),
                "date": commit.get("date", ""),
                "repo_name": repo_name,
            }
            chunks = _chunk_diff(commit_info, parsed)

            for chunk in chunks:
                if not self._store.has_doc(chunk["id"]):
                    all_items.append(chunk)

        if all_items:
            self._store.add_batch(all_items)
            self._store.rebuild()
            self._save_state()

        return {"status": "ok", "chunks_added": len(all_items)}

    # ----- Querying -----

    def query(self, question, top_k=None, model=None, stream=False):
        """
        RAG query: retrieve relevant diffs, build prompt, call LLM.
        Returns the LLM response (or yields chunks if stream=True).
        """
        if top_k is None:
            top_k = cfg.get("top_k", 5)

        # Retrieve relevant diffs
        results = self._store.search(question, top_k=top_k)

        # Build the prompt
        prompt = self._build_prompt(question, results)

        # Call LLM
        if stream:
            return self._ollama.generate(prompt, model=model, stream=True)
        else:
            return self._ollama.generate(prompt, model=model, stream=False)

    def retrieve(self, question, top_k=None):
        """Retrieve relevant diffs without calling LLM."""
        if top_k is None:
            top_k = cfg.get("top_k", 5)
        return self._store.search(question, top_k=top_k)

    def _build_prompt(self, question, context_results):
        """Build the RAG prompt with retrieved diff context."""
        context_blocks = []
        for i, result in enumerate(context_results, 1):
            meta = result["metadata"]
            block = (
                f"--- Context {i} (relevance: {result['score']}) ---\n"
                f"Repository: {meta.get('repo', '?')}\n"
                f"Commit: {meta.get('short_hash', '?')} — {meta.get('message', '?')}\n"
                f"File: {meta.get('file', '?')}\n"
                f"Author: {meta.get('author', '?')} | Date: {meta.get('date', '?')}\n"
                f"\n{result['text']}\n"
            )
            context_blocks.append(block)

        context_text = "\n".join(context_blocks) if context_blocks else "(No relevant code changes found in the index.)"

        prompt = f"""You are Diffy, an AI coding assistant with deep knowledge of a codebase's git history.
You have access to relevant code changes (diffs) from the repository's commit history.
Use these diffs as context to answer the user's question accurately.

When referencing code changes, mention the commit hash, file, and what was changed.
If the context doesn't contain enough information, say so and provide your best answer based on general knowledge.

=== RELEVANT CODE CHANGES ===
{context_text}
=== END CONTEXT ===

User's Question: {question}

Provide a clear, helpful answer:"""

        return prompt

    # ----- Status -----

    def status(self):
        """Return pipeline status."""
        store_stats = self._store.stats()
        return {
            "indexed_repos": len(self._repos),
            "repos": {
                path: {
                    "name": info.get("name"),
                    "last_indexed": info.get("last_indexed_time"),
                    "last_hash": info.get("last_indexed_hash", "")[:7],
                }
                for path, info in self._repos.items()
            },
            "total_chunks": store_stats["documents"],
            "vocabulary_size": store_stats["vocabulary_size"],
            "ollama_available": self._ollama.is_available(),
        }

    def list_repos(self):
        """List indexed repositories."""
        return [
            {
                "path": path,
                "name": info.get("name"),
                "last_indexed": info.get("last_indexed_time"),
            }
            for path, info in self._repos.items()
        ]

    def clear_index(self, repo_path=None):
        """Clear index for a specific repo or all repos."""
        if repo_path:
            abs_path = os.path.abspath(repo_path)
            self._repos.pop(abs_path, None)
            # Note: individual doc removal would require iterating;
            # for simplicity, we rebuild from remaining repos
        else:
            self._repos.clear()
            self._store.clear()
        self._save_state()
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_pipeline = None


def get_pipeline():
    """Get or create the singleton RAG pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline
