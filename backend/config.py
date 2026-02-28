"""
Diffy — Configuration Management
Loads configuration from environment variables, config file, or defaults.
"""

import os
import json

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
}

_config = dict(DEFAULTS)


def _config_path():
    return os.path.join(os.path.expanduser("~"), ".diffy", "config.json")


def load():
    """Load configuration from disk, merging with defaults."""
    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            _config.update(saved)
        except (json.JSONDecodeError, OSError):
            pass  # use defaults on error

    # Environment variables override file config
    env_map = {
        "DIFFY_OLLAMA_URL": "ollama_url",
        "DIFFY_MODEL": "model",
        "DIFFY_INDEX_DIR": "index_dir",
        "DIFFY_WEBHOOK_PORT": "webhook_port",
        "DIFFY_WEBHOOK_SECRET": "webhook_secret",
        "DIFFY_GITHUB_TOKEN": "github_token",
        "DIFFY_MAX_COMMITS": "max_commits",
        "DIFFY_TOP_K": "top_k",
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_config, f, indent=2)


def get(key, default=None):
    """Get a configuration value."""
    return _config.get(key, default)


def set_val(key, value):
    """Set a configuration value (in memory)."""
    _config[key] = value


def update(patch: dict):
    """Update multiple config values and save."""
    _config.update(patch)
    save()


def as_dict():
    """Return a copy of the full config."""
    return dict(_config)


# Auto-load on import
load()
