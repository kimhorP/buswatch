"""Constants and environment-backed configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load .env from cwd (or nearest parent) if present — real env vars win
load_dotenv()

# --- Fixed product decisions (see docs/system-design.md) ---

THRESHOLD = 7  # alert when seats drop to this or below
POLL_TIMES: tuple[time, time, time] = (time(9, 0), time(15, 0), time(22, 0))
TZ = ZoneInfo("Asia/Phnom_Penh")

# --- Environment-backed ---


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    db_path: str
    web_port: int


def load_settings() -> Settings:
    token = os.environ.get("BOT_TOKEN", "")
    db_path = os.environ.get("DB_PATH", "data/buswatch.db")
    try:
        web_port = int(os.environ.get("WEB_PORT", "8080"))
    except ValueError:
        web_port = 8080

    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    return Settings(bot_token=token, db_path=db_path, web_port=web_port)
