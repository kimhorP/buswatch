"""Entrypoint: python -m buswatch"""

from buswatch.bot import run

if __name__ == "__main__":
    import asyncio

    asyncio.run(run())
