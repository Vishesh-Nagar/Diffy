"""
Diffy — Configuration Management
Loads configuration from .env file, environment variables, config file, or defaults.

Priority order (highest to lowest):
  1. Environment variables (DIFFY_*)
  2. .env file values
  3. config.json file values
  4. Built-in defaults
"""

import os
import json
import urllib.parse

from dotenv import load_dotenv as _load_dotenv

# Default configuration
DEFAULTS = {
    "ollama_url": "http://localhost:11434",
    "model": "codellama",
    "index_dir": os.path.join(os.path.expanduser("~"), ".diffy", "index"),
    "webhook_port": 9417,
    "webhook_secret": "",
    "github_token": "",
    "max_commits": 200,
    "top_k": 5,
    "chunk_max_lines": 80,
    # Cloud LLM API keys (leave empty to use Ollama)
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "openai_base_url": "https://api.openai.com/v1",
    "anthropic_api_key": "",
    "anthropic_model": "claude-sonnet-4-20250514",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.0-flash",
    "embed_model": "nomic-embed-text",
}

_config = dict(DEFAULTS)


def _config_path():
    return os.path.join(os.path.expanduser("~"), ".diffy", "config.json")


def _find_env_file():
    """Locate the .env file, checking backend dir and project root."""
    # Check the directory this file lives in (backend/)
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(backend_dir, ".env")
    if os.path.isfile(candidate):
        return candidate

    # Check the project root (one level up from backend/)
    project_root = os.path.dirname(backend_dir)
    candidate = os.path.join(project_root, ".env")
    if os.path.isfile(candidate):
        return candidate

    return None


def load():
    """Load configuration from .env file, disk config, and env vars."""
    # --- 1. Load .env file (populates os.environ without overriding) ---
    env_path = _find_env_file()
    if env_path:
        _load_dotenv(dotenv_path=env_path, override=False)

    # --- 2. Load config.json from disk ---
    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            _config.update(saved)
        except (json.JSONDecodeError, OSError) as e:
            import sys
            print(f"Warning: Failed to load config.json: {e}", file=sys.stderr)
            pass  # use defaults on error

    # --- 3. Environment variables override everything else ---
    env_map = {
        "DIFFY_OLLAMA_URL": "ollama_url",
        "DIFFY_MODEL": "model",
        "DIFFY_INDEX_DIR": "index_dir",
        "DIFFY_WEBHOOK_PORT": "webhook_port",
        "DIFFY_WEBHOOK_SECRET": "webhook_secret",
        "DIFFY_GITHUB_TOKEN": "github_token",
        "DIFFY_MAX_COMMITS": "max_commits",
        "DIFFY_TOP_K": "top_k",
        # Cloud LLM keys
        "DIFFY_OPENAI_API_KEY": "openai_api_key",
        "DIFFY_OPENAI_MODEL": "openai_model",
        "DIFFY_OPENAI_BASE_URL": "openai_base_url",
        "DIFFY_ANTHROPIC_API_KEY": "anthropic_api_key",
        "DIFFY_ANTHROPIC_MODEL": "anthropic_model",
        "DIFFY_GEMINI_API_KEY": "gemini_api_key",
        "DIFFY_GEMINI_MODEL": "gemini_model",
        "DIFFY_EMBED_MODEL": "embed_model",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            # attempt int conversion for numeric fields
            if cfg_key in ("webhook_port", "max_commits", "top_k"):
                try:
                    val = int(val)
                except ValueError:
                    pass
            _config[cfg_key] = val

    # Ensure index directory exists
    os.makedirs(_config["index_dir"], exist_ok=True)
    return _config


def save():
    """Persist current configuration to disk."""
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    # Strip out sensitive keys
    sensitive_keys = {"github_token", "openai_api_key", "anthropic_api_key", "gemini_api_key", "webhook_secret"}
    safe_config = {k: v for k, v in _config.items() if k not in sensitive_keys}

    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe_config, f, indent=2)


def get(key, default=None):
    """Get a configuration value."""
    return _config.get(key, default)


def _is_valid_url(url):
    try:
        result = urllib.parse.urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


def _sanitize_and_whitelist(key, value):
    if key not in DEFAULTS:
        raise ValueError(f"Invalid configuration key: {key}")
    
    if key in ("ollama_url", "openai_base_url"):
        if not _is_valid_url(value):
            raise ValueError(f"Invalid URL for {key}: {value}")
            
    return value


def set_val(key, value):
    """Set a configuration value (in memory)."""
    try:
        value = _sanitize_and_whitelist(key, value)
        _config[key] = value
    except ValueError as e:
        import sys
        print(f"Warning: {e}", file=sys.stderr)


def update(patch: dict):
    """Update multiple config values and save."""
    valid_patch = {}
    for k, v in patch.items():
        try:
            valid_patch[k] = _sanitize_and_whitelist(k, v)
        except ValueError as e:
            import sys
            print(f"Warning: {e}", file=sys.stderr)
            
    _config.update(valid_patch)
    save()


def as_dict():
    """Return a copy of the full config."""
    return dict(_config)


# Auto-load on import
load()
