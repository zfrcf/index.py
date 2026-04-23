import os
import json
from config import (
    DATA_DIR,
    GIVEAWAYS_FILE,
    TICKETS_FILE,
    VERIFY_FILE,
    BLACKLIST_FILE,
    SECURITY_FILE,
)

os.makedirs(DATA_DIR, exist_ok=True)


def ensure_file(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4, ensure_ascii=False)


def init_storage():
    ensure_file(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
    ensure_file(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
    ensure_file(VERIFY_FILE, {"users": {}, "panel_message_id": None})
    ensure_file(BLACKLIST_FILE, {"banned_ids": []})
    ensure_file(
        SECURITY_FILE,
        {
            "raid_mode": False,
            "raid_until": None,
            "locked_channels": [],
            "join_timestamps": [],
        },
    )


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
