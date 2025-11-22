import json
import os
from typing import Dict, Any

DEFAULTS = {
    "cycle_delay_seconds": 3600,
    "search_delay_seconds": 2,
    "log_dir": "logs",
    "max_log_size_mb": 50,
    "telegram_bot_token": "",
    "telegram_chat_id": ""
}

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "api_config.json")

def load_config() -> Dict[str, Any]:
    """Load configuration from *api_config.json* and merge with defaults.

    The function returns a dictionary containing all required keys for the
    periodic fetch system. Missing optional keys are filled with sensible
    defaults defined in ``DEFAULTS``.
    """
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)

    # Merge defaults – user values take precedence
    cfg = {**DEFAULTS, **user_cfg}

    # Backward‑compatible flattening of telegram credentials
    telegram_cfg = cfg.get("telegram", {})
    if isinstance(telegram_cfg, dict):
        if "bot_token" in telegram_cfg:
            cfg["telegram_bot_token"] = telegram_cfg["bot_token"]
        if "chat_id" in telegram_cfg:
            cfg["telegram_chat_id"] = telegram_cfg["chat_id"]

    return cfg
