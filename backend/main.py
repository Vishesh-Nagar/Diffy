"""
Diffy — Backend Main Entry Point
JSON-RPC server over stdin/stdout for communication with the VS Code extension.
Also starts the webhook server for GitHub push notifications.
"""

import sys
import json
import os
import threading
import traceback

# Ensure backend directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import rag_pipeline as rag
import webhook_server as webhook
import llm_client as llm
import git_integration as git


# ---------------------------------------------------------------------------
# JSON-RPC Protocol
# ---------------------------------------------------------------------------

def _send_response(req_id, result=None, error=None):
    """Send a JSON-RPC response to stdout."""
    response = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        response["error"] = {"code": -1, "message": str(error)}
    else:
        response["result"] = result

    line = json.dumps(response, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def _send_notification(method, params=None):
    """Send a JSON-RPC notification (no id, no response expected)."""
    notif = {"jsonrpc": "2.0", "method": method}
    if params:
        notif["params"] = params
    line = json.dumps(notif, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Request Handlers
# ---------------------------------------------------------------------------

def handle_index(params):
    """Index a git repository."""
    repo_path = params.get("repoPath", "")
    force = params.get("force", False)

    if not repo_path:
        return {"status": "error", "message": "repoPath is required"}

    pipeline = rag.get_pipeline()
    return pipeline.index_repository(repo_path, force=force)


def handle_query(params):
    """Query the RAG pipeline (non-streaming)."""
    question = params.get("question", "")
    top_k = params.get("topK", None)
    model = params.get("model", None)

    if not question:
        return {"status": "error", "message": "question is required"}

    pipeline = rag.get_pipeline()
    response = pipeline.query(question, top_k=top_k, model=model, stream=False)
    return {"status": "ok", "response": response}


def handle_query_stream(params, req_id):
    """Query with streaming — sends chunks as notifications."""
    question = params.get("question", "")
    top_k = params.get("topK", None)
    model = params.get("model", None)

    if not question:
        _send_response(req_id, error="question is required")
        return None  # signal that we already sent a response

    pipeline = rag.get_pipeline()

    # First retrieve context and send it
    context = pipeline.retrieve(question, top_k=top_k)
    context_summary = []
    for r in context:
        m = r["metadata"]
        context_summary.append({
            "score": r["score"],
            "commit": m.get("short_hash", ""),
            "message": m.get("message", ""),
            "file": m.get("file", ""),
            "repo": m.get("repo", ""),
        })

    # Send context as first notification
    _send_notification("stream/context", {"requestId": req_id, "context": context_summary})

    # Stream LLM response
    try:
        for chunk in pipeline.query(question, top_k=top_k, model=model, stream=True):
            _send_notification("stream/chunk", {"requestId": req_id, "text": chunk})
        _send_notification("stream/done", {"requestId": req_id})
    except Exception as e:
        _send_notification("stream/error", {"requestId": req_id, "error": str(e)})

    return {"status": "ok", "streamed": True}


def handle_retrieve(params):
    """Retrieve relevant diffs without calling LLM."""
    question = params.get("question", "")
    top_k = params.get("topK", 5)

    pipeline = rag.get_pipeline()
    results = pipeline.retrieve(question, top_k=top_k)
    return {"status": "ok", "results": results}


def handle_status(_params):
    """Get pipeline and system status."""
    pipeline = rag.get_pipeline()
    status = pipeline.status()
    status["webhook_running"] = webhook.is_running()
    return status


def handle_list_repos(_params):
    """List indexed repositories."""
    pipeline = rag.get_pipeline()
    return {"repos": pipeline.list_repos()}


def handle_list_models(_params):
    """List available models from the active LLM provider."""
    client = llm.LLMClient()
    return {"models": client.list_models(), "provider": client.provider_name}


def handle_clear_index(params):
    """Clear index for a repo or all repos."""
    repo_path = params.get("repoPath", None)
    pipeline = rag.get_pipeline()
    return pipeline.clear_index(repo_path)


def handle_set_config(params):
    """Update configuration."""
    cfg.update(params)
    return {"status": "ok", "config": cfg.as_dict()}


def handle_get_config(_params):
    """Get current configuration."""
    return cfg.as_dict()


def handle_index_diffs(params):
    """Index pre-fetched diffs (from webhook)."""
    pipeline = rag.get_pipeline()
    return pipeline.index_diffs(params)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_HANDLERS = {
    "index": handle_index,
    "query": handle_query,
    "queryStream": handle_query_stream,
    "retrieve": handle_retrieve,
    "status": handle_status,
    "listRepos": handle_list_repos,
    "listModels": handle_list_models,
    "clearIndex": handle_clear_index,
    "setConfig": handle_set_config,
    "getConfig": handle_get_config,
    "indexDiffs": handle_index_diffs,
}


def dispatch(request):
    """Dispatch a JSON-RPC request to the appropriate handler."""
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    handler = _HANDLERS.get(method)
    if not handler:
        _send_response(req_id, error=f"Unknown method: {method}")
        return

    try:
        # queryStream is special: it handles its own response
        if method == "queryStream":
            result = handler(params, req_id)
            if result is not None:
                _send_response(req_id, result=result)
        else:
            result = handler(params)
            _send_response(req_id, result=result)
    except Exception as e:
        _send_response(req_id, error=f"{type(e).__name__}: {e}")
        # Log error to stderr (visible in VS Code's output channel)
        traceback.print_exc(file=sys.stderr)


# ---------------------------------------------------------------------------
# Webhook callback: when GitHub sends a push event
# ---------------------------------------------------------------------------

def _on_webhook_push(event_data):
    """Called when a push event is received from GitHub webhook."""
    repo_full_name = event_data.get("repo_full_name", "")
    commits = event_data.get("commits", [])

    if not commits:
        return

    # Parse owner/repo
    parts = repo_full_name.split("/", 1)
    if len(parts) != 2:
        return
    owner, repo = parts

    # Fetch diffs for each commit via GitHub API
    enriched_commits = []
    for c in commits:
        raw_diff = git.fetch_remote_diff(owner, repo, c["id"])
        if raw_diff:
            enriched_commits.append({
                "hash": c["id"],
                "message": c.get("message", ""),
                "author": c.get("author", ""),
                "date": c.get("timestamp", ""),
                "raw_diff": raw_diff,
            })

    if enriched_commits:
        pipeline = rag.get_pipeline()
        result = pipeline.index_diffs({
            "repo_name": repo,
            "commits": enriched_commits,
        })

        # Notify VS Code extension that new diffs were indexed
        _send_notification("webhook/indexed", {
            "repo": repo_full_name,
            "commits_indexed": len(enriched_commits),
            "chunks_added": result.get("chunks_added", 0),
        })


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    """Main entry point: read JSON-RPC requests from stdin."""
    # Start webhook server
    try:
        webhook.set_push_callback(_on_webhook_push)
        webhook.start()
    except Exception as e:
        print(f"Warning: webhook server failed to start: {e}", file=sys.stderr)

    # Send ready notification
    _send_notification("ready", {
        "version": "1.0.0",
        "webhook_port": cfg.get("webhook_port", 9417),
    })

    # Read requests from stdin
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Handle in a thread to not block stdin reading
        threading.Thread(
            target=dispatch,
            args=(request,),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()
