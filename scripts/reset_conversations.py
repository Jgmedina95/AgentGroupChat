from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from db.session import SessionLocal


DEFAULT_API_BASE_URL = "http://localhost:8000/api"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete all conversations.")
    parser.add_argument(
        "--mode",
        choices=["auto", "api", "db"],
        default="auto",
        help="Use API deletes when available for live TUI updates, or delete directly from the database.",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help="API base URL used in api/auto mode.",
    )
    return parser.parse_args()


def api_is_available(base_url: str) -> bool:
    try:
        with httpx.Client(base_url=base_url, timeout=2.0) as client:
            response = client.get("/conversations")
            response.raise_for_status()
            return True
    except httpx.HTTPError:
        return False


def delete_via_api(base_url: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        response = client.get("/conversations")
        response.raise_for_status()
        conversations = response.json()
        for conversation in conversations:
            delete_response = client.delete(f"/conversations/{conversation['id']}")
            delete_response.raise_for_status()
        print(f"Deleted {len(conversations)} conversation(s).")


def delete_via_db() -> None:
    db = SessionLocal()
    try:
        deleted_count = db.execute("SELECT COUNT(*) AS conversation_count FROM conversations").fetchone()["conversation_count"]
        db.execute("DELETE FROM conversations")
        db.commit()
    finally:
        db.close()

    print(f"Deleted {deleted_count} conversation(s).")


def main() -> None:
    args = parse_args()

    if args.mode == "db":
        delete_via_db()
        return

    if args.mode == "api":
        delete_via_api(args.api_base_url)
        return

    if api_is_available(args.api_base_url):
        delete_via_api(args.api_base_url)
        return

    delete_via_db()


if __name__ == "__main__":
    main()