# BusWatch

A Telegram bot that watches Cambodian bus ticket availability on the Phnom Penh ⇄ Siem Reap corridor, so you don't have to keep refreshing the booking page.

- **Monitors availability** — checks seat counts for the routes and departures you care about
- **Tracks changes over time** — keeps seat-count history, so you can see when tickets appeared or vanished
- **Alerts you** — sends a Telegram message when seats drop to ≤ 7, or a departure is sold out

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/kimhorP/buswatch
cd buswatch
uv sync

cp .env.example .env
# edit .env and set BOT_TOKEN (from @BotFather)

uv run python -m buswatch
```

That starts everything in one process: the Telegram bot (long polling), the watcher loop, and the local web page.

## Configuration

`.env`:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token (API key) from [@BotFather](https://t.me/BotFather) |

## Bot commands

| Command | What it does |
|---|---|
| `/start` | Greeting and command list |
| `/watch [date]` | Follow a direction (`dd-mm-yy` date, e.g. `20-10-26`) and get alerts |
| `/check [date]` | Live on-demand seat lookup — date → direction → company → vehicle → time |
| `/unwatch` | Stop alerts |

## Data sources

| Company | Method |
|---|---|
| Larryta | JSON API (CSRF handshake) |
| Saly VIP | JSON API |
| CamboLink21 | HTML scrape of BookMeBus |

## Development

```bash
uv run pytest          # tests (parser tests read fixtures in captures/, skip if absent)
uv run ruff check      # lint
uv run ty check        # type check
```
