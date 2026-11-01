"""Local-only web page: a table of every user of the bot (see security.md).

Bound to 127.0.0.1 by default — not exposed. To view from another machine,
use an SSH tunnel: ssh -L 8080:127.0.0.1:8080 <vps>
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging

from buswatch.db import Database

log = logging.getLogger("buswatch.web")

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BusWatch users</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #fafafa; color: #222; }}
  h1 {{ font-size: 1.2rem; }}
  table {{ border-collapse: collapse; background: #fff; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; font-size: 0.9rem; }}
  th {{ background: #f0f0f0; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  .none {{ color: #999; font-style: italic; }}
</style>
</head>
<body>
<h1>BusWatch users ({count})</h1>
<table>
 <tr>
 <th>#</th><th>Name</th><th>Username</th><th>Chat ID</th>
 <th>Direction</th><th>Company</th><th>Watch date</th><th>First seen</th>
 </tr>
{rows}
</table>
</body>
</html>"""


def _esc(value: object) -> str:
    if value is None or value == "":
        return '<span class="none">—</span>'
    return html.escape(str(value))


def render_users_page(users: list[dict]) -> str:
    rows = []
    for i, u in enumerate(users, 1):
        direction = None
        if u.get("direction"):
            label = (
                "Phnom Penh → Siem Reap" if u["direction"] == "PP_SR" else "Siem Reap → Phnom Penh"
            )
            direction = label
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{_esc(u.get('name'))}</td>"
            f"<td>{_esc(u.get('username'))}</td>"
            f"<td>{u['chat_id']}</td>"
            f"<td>{_esc(direction)}</td>"
            f"<td>{_esc(u.get('company') or 'all')}</td>"
            f"<td>{_esc(u.get('watch_date'))}</td>"
            f"<td>{_esc(u.get('first_seen'))}</td>"
            "</tr>"
        )
    return _PAGE.format(count=len(users), rows="\n".join(rows))


async def _handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, db: Database
) -> None:
    try:
        await asyncio.wait_for(reader.read(1024), timeout=5)
        try:
            users = await db.list_users()
            body = render_users_page(users).encode()
            status = "200 OK"
        except Exception:
            log.exception("Failed to render users page")
            body = b"<h1>500 Internal Server Error</h1>"
            status = "500 Internal Server Error"
        writer.write(
            b"HTTP/1.1 " + status.encode() + b"\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        await writer.drain()
    except Exception:
        log.exception("Web request failed")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def serve(db: Database, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the users table page until cancelled."""

    async def _on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_client(reader, writer, db)

    server = await asyncio.start_server(_on_connect, host, port)
    log.info("Users table at http://%s:%d/", host, port)
    async with server:
        await server.serve_forever()
