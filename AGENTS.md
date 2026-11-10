# BusWatch - Agent Instructions

## Overview
Telegram bot that watches Cambodian bus ticket availability on the Phnom Penh ⇄ Siem Reap corridor and alerts users when seats drop to ≤7.

## Quick Start
```bash
git clone https://github.com/kimhorP/buswatch
cd buswatch
cp .env.example .env
# Edit .env and set BOT_TOKEN from @BotFather
uv sync
uv run python -m buswatch
```

## Core Commands
- `/start` - Greet and show available commands
- `/watch [dd-mm-yy]` - Start watching a date for a direction (PP_SR or SR_PP), each call adds a new watch
- `/check [dd-mm-yy]` - Live seat availability check for a date
- `/unwatch` - List current watches and stop one, or stop all if only one exists

## Architecture
- **Bot** (`src/buswatch/bot.py`) - Telegram command handler
- **Watcher** (`src/buswatch/watcher.py`) - Background poller (runs every 15 min)
- **Database** (`src/buswatch/db.py`) - SQLite persistence for settings & seat history
- **Sources** (`src/buswatch/sources/`) - Three adapters:
  - `Larryta` - JSON API with CSRF handshake
  - `SalyVIP` - JSON API
  - `CamboLink21` - HTML scraping via BookMeBus
- **Web** (`src/buswatch/web.py`) - Local users table page

## Setup & Configuration
- **Python**: 3.12+
- **Build tool**: `uv` (used for `uv sync`, `uv run`)
- **Dependencies**: aiosqlite, httpx, python-dotenv, python-telegram-bot, ruff, ty
- **Environment** (`.env`):
  - `BOT_TOKEN` - Required Telegram bot token
  - `DB_PATH` - SQLite database path (default: `data/buswatch.db`)
  - `WEB_PORT` - Web server port (default: 8080)

## Testing
- Unit tests in `tests/` cover:
  - Date parsing (`_parse_user_date`, `_to_24h`)
  - Web page rendering
  - Source parsing (Larryta, SalyVIP, CamboLink21)
  - Database operations
- Run tests: `uv run pytest`

## CI/CD
- GitHub Actions in `.github/workflows/deploy.yml`:
  - Push triggers: lint (`ruff check`), type check (`ty check`), tests (`pytest`)
  - Deploy: SSH to VPS, pull code, sync, restart service

## Key Facts for Agents
- **Poll interval**: 15 minutes (POLL_INTERVAL = 15 min)
- **Alert threshold**: ≤7 seats (fixed, not configurable)
- **Watched direction**: Multiple per user (each `/watch` adds a new watch)
- **Re-alert interval**: Every 6 hours (REALERT_INTERVAL) while seats ≤7
- **Data sources**: Larryta (JSON), SalyVIP (JSON), CamboLink21 (HTML)
- **Database schema**: `settings` (id, chat_id, direction, company, watch_date, updated_at) + `seat_history` (chat_id, company, date, direction, depart_time, vehicle, seats_available, etc.)
- **Web page**: Lists all users with their watch status (direction, company, watch date)
- **Seed data**: `data/buswatch.db` is created on first run via `uv sync`
- **Security**: see `security.md` for secrets handling, third-party site interaction rules, and Telegram security guidelines

## Common Tasks
1. **Start the bot**: `uv run python -m buswatch`
2. **Add a user watch**: `/watch 2026-10-26` (sets direction, date)
3. **Check seats**: `/check 2026-10-26`
4. **Stop watching**: `/unwatch` (lists watches with inline buttons)
5. **View users**: Web server at `http://127.0.0.1:8080`
6. **Run tests**: `uv run pytest`
7. **Lint code**: `uv run ruff check`
8. **Type check**: `uv run ty check`

## Important Notes
- The bot runs in the same process as the watcher (background task)
- Seat history is stored in SQLite; each trip is keyed by (company, date, direction, depart_time, vehicle)
- Alert threshold is fixed at 7 seats — once a user is alerted for a trip, they are not re-alerted while still ≤7 until REALERT_INTERVAL (6 hours) has passed
- On-demand `/check` bypasses the schedule and gives a live snapshot
- All source adapters follow the same interface: `fetch(day, direction) -> list[NormalizedTrip]`
- Multiple watches per user are supported — each `/watch` creates a new row in `settings`
