"""
Diffy — GitHub Webhook Receiver
Lightweight HTTP server that listens for GitHub webhook payloads,
validates HMAC signatures, and signals the backend to index new diffs.
Runs on a configurable local port (default: 9417).
"""

import json
import hmac
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer, BaseHTTPRequestHandler

import config as cfg


# Callback that will be set by the main module
_on_push_callback = None


def set_push_callback(callback):
    """
    Set the callback function to invoke when a push event is received.
    callback(event_data) where event_data = {
        repo_full_name, ref, commits: [{id, message, added, removed, modified}], ...
    }
    """
    global _on_push_callback
    _on_push_callback = callback


def _verify_signature(payload_body, signature_header, secret):
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        return False  # secret must be configured

    if not signature_header:
        return False

    if signature_header.startswith("sha256="):
        expected = signature_header[7:]
    else:
        return False

    mac = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), expected)


class WebhookHandler(BaseHTTPRequestHandler):
    """Handle incoming GitHub webhook POST requests."""

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass

    def do_POST(self):
        """Handle POST /webhook."""
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0 or content_length > 10 * 1024 * 1024:  # 10MB limit
            self.send_response(400)
            self.end_headers()
            return

        body = self.rfile.read(content_length)

        # Verify signature
        secret = cfg.get("webhook_secret", "")
        sig = self.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(body, sig, secret):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "invalid signature"}')
            return

        # Parse event
        event_type = self.headers.get("X-GitHub-Event", "")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_response(400)
            self.end_headers()
            return

        # Handle push events
        if event_type == "push":
            event_data = self._parse_push(payload)
            if _on_push_callback and event_data:
                # Submit to bounded pool instead of spawning an unlimited thread
                # (fixes TODO #13 — prevents thread exhaustion under webhook floods)
                _executor.submit(_on_push_callback, event_data)

        # Handle ping (webhook setup verification)
        elif event_type == "ping":
            pass  # just respond 200

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode())

    def do_GET(self):
        """Health check endpoint."""
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "running"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    @staticmethod
    def _parse_push(payload):
        """Extract relevant data from a GitHub push event payload."""
        repo = payload.get("repository", {})
        commits = []
        for c in payload.get("commits", []):
            commits.append({
                "id": c.get("id", ""),
                "message": c.get("message", ""),
                "author": c.get("author", {}).get("name", ""),
                "timestamp": c.get("timestamp", ""),
                "added": c.get("added", []),
                "removed": c.get("removed", []),
                "modified": c.get("modified", []),
            })

        if not commits:
            return None

        return {
            "repo_full_name": repo.get("full_name", ""),
            "repo_clone_url": repo.get("clone_url", ""),
            "ref": payload.get("ref", ""),
            "before": payload.get("before", ""),
            "after": payload.get("after", ""),
            "commits": commits,
            "pusher": payload.get("pusher", {}).get("name", ""),
        }


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

_server = None
_thread = None
# Bounded thread pool: caps concurrent webhook processing at 4 workers.
# Excess requests queue rather than spawning unlimited threads (fixes TODO #13).
_executor = ThreadPoolExecutor(max_workers=4)


def start(port=None):
    """Start the webhook server in a background thread."""
    global _server, _thread

    if _server is not None:
        return  # already running

    port = port or cfg.get("webhook_port", 9417)

    _server = HTTPServer(("127.0.0.1", port), WebhookHandler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()


def stop():
    """Stop the webhook server."""
    global _server, _thread
    if _server:
        _server.shutdown()
        _server = None
        _thread = None


def is_running():
    """Check if the webhook server is running."""
    return _server is not None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    def _test_callback(event):
        print(f"Push received: {event['repo_full_name']}")
        print(f"  Commits: {len(event['commits'])}")
        for c in event["commits"]:
            print(f"  - {c['id'][:7]}: {c['message'][:60]}")

    set_push_callback(_test_callback)
    port = 9417
    print(f"Starting webhook server on port {port}...")
    start(port)
    print(f"Listening on http://127.0.0.1:{port}/webhook")
    print("Send a test: curl -X POST http://127.0.0.1:9417/webhook ...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop()
        print("\nStopped.")
