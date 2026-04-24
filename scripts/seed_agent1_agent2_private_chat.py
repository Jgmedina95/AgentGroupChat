from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import select


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from db.session import SessionLocal
from models import Agent
from services.message_service import create_conversation, create_message


AGENT_SPECS = {
    "agent1": "tipster",
    "agent2": "tipster",
}

DEFAULT_API_BASE_URL = "http://localhost:8000/api"
DEFAULT_ACTION_DELAY_SECONDS = 1.5
DEFAULT_MESSAGE_DELAY_SECONDS = 2.0


def resolve_agent(db, display_name: str, agent_type: str) -> Agent:
    matches = list(
        db.scalars(
            select(Agent)
            .where(Agent.display_name == display_name, Agent.type == agent_type)
            .order_by(Agent.id.asc())
        ).all()
    )

    if not matches:
        raise SystemExit(f"Missing required agent: {display_name} ({agent_type})")

    if len(matches) > 1:
        chosen = matches[-1]
        duplicate_ids = ", ".join(agent.id for agent in matches)
        print(
            f"Multiple matches for {display_name} ({agent_type}). "
            f"Using {chosen.id}. Candidates: {duplicate_ids}"
        )
        return chosen

    return matches[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed a private chat between agent1 and agent2.")
    parser.add_argument(
        "--mode",
        choices=["auto", "api", "db"],
        default="auto",
        help="Use API calls when available for live TUI updates, or write directly to the database.",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help="API base URL used in api/auto mode.",
    )
    parser.add_argument(
        "--action-delay",
        type=float,
        default=DEFAULT_ACTION_DELAY_SECONDS,
        help="Seconds to wait after creating the conversation before the first message.",
    )
    parser.add_argument(
        "--message-delay",
        type=float,
        default=DEFAULT_MESSAGE_DELAY_SECONDS,
        help="Seconds to wait between messages.",
    )
    parser.add_argument(
        "--no-delay",
        action="store_true",
        help="Disable simulated delays for fast setup.",
    )
    return parser.parse_args()


def sleep_with_log(seconds: float, label: str) -> None:
    if seconds <= 0:
        return
    print(f"Waiting {seconds:.1f}s before {label}...")
    time.sleep(seconds)


def resolve_agents_via_api(client: httpx.Client) -> dict[str, dict]:
    response = client.get("/agents")
    response.raise_for_status()
    payload = response.json()

    resolved: dict[str, dict] = {}
    for name, agent_type in AGENT_SPECS.items():
        matches = [agent for agent in payload if agent["display_name"] == name and agent["type"] == agent_type]
        if not matches:
            raise SystemExit(f"Missing required agent: {name} ({agent_type})")
        if len(matches) > 1:
            chosen = matches[-1]
            duplicate_ids = ", ".join(agent["id"] for agent in matches)
            print(
                f"Multiple matches for {name} ({agent_type}). "
                f"Using {chosen['id']}. Candidates: {duplicate_ids}"
            )
            resolved[name] = chosen
        else:
            resolved[name] = matches[0]
    return resolved


def conversation_messages() -> list[tuple[str, str]]:
    return [
        ("agent1", "Hey, did you see the way agent3 reacted in the other chat?"),
        ("agent2", "Yeah, it looked like agent3 was trying to keep the group calm."),
        ("agent1", "That was my read too. I think it helped that we kept the plan simple."),
        ("agent2", "Agreed. Let us keep this one between us for now and see how things develop."),
    ]


def seed_via_api(args: argparse.Namespace) -> None:
    with httpx.Client(base_url=args.api_base_url, timeout=10.0) as client:
        agents = resolve_agents_via_api(client)
        conversation = client.post(
            "/conversations",
            json={
                "type": "direct",
                "title": "agent1-agent2-private",
                "participant_ids": [agents["agent1"]["id"], agents["agent2"]["id"]],
            },
        )
        conversation.raise_for_status()
        conversation_payload = conversation.json()
        print(f"Created conversation: {conversation_payload['title']} ({conversation_payload['id']})")

        if not args.no_delay:
            sleep_with_log(args.action_delay, "starting the private chat")

        for sender_name, content in conversation_messages():
            response = client.post(
                "/messages",
                json={
                    "conversation_id": conversation_payload["id"],
                    "sender_id": agents[sender_name]["id"],
                    "content": content,
                },
            )
            response.raise_for_status()
            print(f"[{conversation_payload['title']}] {sender_name}: {content}")
            if not args.no_delay:
                sleep_with_log(args.message_delay, "the next private message")


def api_is_available(base_url: str) -> bool:
    try:
        with httpx.Client(base_url=base_url, timeout=2.0) as client:
            response = client.get("/agents")
            response.raise_for_status()
            return True
    except httpx.HTTPError:
        return False


def seed_via_db(args: argparse.Namespace) -> None:
    with SessionLocal() as db:
        agents = {
            name: resolve_agent(db, name, agent_type)
            for name, agent_type in AGENT_SPECS.items()
        }

        conversation = create_conversation(
            db,
            conversation_type="direct",
            title="agent1-agent2-private",
            participant_ids=[agents["agent1"].id, agents["agent2"].id],
        )
        print(f"Created conversation: {conversation.title} ({conversation.id})")

        if not args.no_delay:
            sleep_with_log(args.action_delay, "starting the private chat")

        for sender_name, content in conversation_messages():
            create_message(db, conversation.id, agents[sender_name].id, content)
            print(f"[{conversation.title}] {sender_name}: {content}")
            if not args.no_delay:
                sleep_with_log(args.message_delay, "the next private message")


def main() -> None:
    args = parse_args()

    if args.mode == "db":
        seed_via_db(args)
        return

    if args.mode == "api":
        seed_via_api(args)
        return

    if api_is_available(args.api_base_url):
        print(f"API available at {args.api_base_url}. Using API mode for live TUI updates.")
        seed_via_api(args)
        return

    print("API is not available. Falling back to direct database mode.")
    seed_via_db(args)


if __name__ == "__main__":
    main()