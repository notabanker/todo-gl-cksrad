"""Packaged backend entry point for the native macOS application."""

import argparse
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="To-Do Gambling local desktop service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def run() -> None:
    args = parse_args()

    # database.py creates its engine at import time, so configure the writable
    # Application Support path before importing the FastAPI app.
    os.environ["TYCHE_DB_PATH"] = args.db
    os.environ["TYCHE_NO_BROWSER"] = "1"

    import uvicorn

    from main import app

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        loop="asyncio",
        http="h11",
        access_log=False,
    )


if __name__ == "__main__":
    run()
