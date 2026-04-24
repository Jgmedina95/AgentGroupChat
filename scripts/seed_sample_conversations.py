from __future__ import annotations

import argparse
import time
import sys
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
    "agent3": "user",
    "agent4": "user",
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
    parser = argparse.ArgumentParser(description="Seed sample conversations with human-like timing.")
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
        help="Seconds to wait after creating a conversation before the next action.",
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


def seed_via_api(args: argparse.Namespace) -> None:
    with httpx.Client(base_url=args.api_base_url, timeout=10.0) as client:
        agents = resolve_agents_via_api(client)

        group_conversation = client.post(
            "/conversations",
            json={
                "type": "group",
                "title": "agent1-agent4",
                "participant_ids": [
                    agents["agent1"]["id"],
                    agents["agent2"]["id"],
                    agents["agent3"]["id"],
                    agents["agent4"]["id"],
                ],
            },
        )
        group_conversation.raise_for_status()
        group_payload = group_conversation.json()
        print(f"Created conversation: {group_payload['title']} ({group_payload['id']})")

        if not args.no_delay:
            sleep_with_log(args.action_delay, "creating the private conversation")

        private_conversation = client.post(
            "/conversations",
            json={
                "type": "direct",
                "title": "agent2-agent3-private",
                "participant_ids": [agents["agent2"]["id"], agents["agent3"]["id"]],
            },
        )
        private_conversation.raise_for_status()
        private_payload = private_conversation.json()
        print(f"Created conversation: {private_payload['title']} ({private_payload['id']})")

        group_messages = [
            (agents["agent3"]["id"], "Hey everyone, thanks for joining. What is the plan for tonight?"),
            (agents["agent1"]["id"], "I think we should keep it simple and meet around 8."),
            (agents["agent4"]["id"], "Works for me. I can bring snacks if we need them."),
            (agents["agent2"]["id"], "Good call. I will confirm the location in a bit."),
            (agents["agent1"]["id"], "Perfect. Let us keep the group posted if anything changes."),
        ]

        private_messages = [
            (agents["agent2"]["id"], "Between us, do you think agent1 is actually organized for tonight?"),
            (agents["agent3"]["id"], "Mostly yes, but agent1 always sounds more confident than the real plan."),
            (agents["agent2"]["id"], "That is exactly my read. I will double check the location before everyone shows up."),
            (agents["agent3"]["id"], "Smart. Better we sort it out quietly before the group starts asking questions."),
        ]

        if not args.no_delay:
            sleep_with_log(args.action_delay, "starting the group conversation")

        for sender_id, content in group_messages:
            response = client.post(
                "/messages",
                json={
                    "conversation_id": group_payload["id"],
                    "sender_id": sender_id,
                    "content": content,
                },
            )
            response.raise_for_status()
            sender_name = next(name for name, agent in agents.items() if agent["id"] == sender_id)
            print(f"[{group_payload['title']}] {sender_name}: {content}")
            if not args.no_delay:
                sleep_with_log(args.message_delay, "the next group message")

        if not args.no_delay:
            sleep_with_log(args.action_delay, "starting the private conversation")

        for sender_id, content in private_messages:
            response = client.post(
                "/messages",
                json={
                    "conversation_id": private_payload["id"],
                    "sender_id": sender_id,
                    "content": content,
                },
            )
            response.raise_for_status()
            sender_name = next(name for name, agent in agents.items() if agent["id"] == sender_id)
            print(f"[{private_payload['title']}] {sender_name}: {content}")
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

        group_conversation = create_conversation(
            db,
            conversation_type="group",
            title="agent1-agent4",
            participant_ids=[
                agents["agent1"].id,
                agents["agent2"].id,
                agents["agent3"].id,
                agents["agent4"].id,
            ],
        )
        print(f"Created conversation: {group_conversation.title} ({group_conversation.id})")

        if not args.no_delay:
            sleep_with_log(args.action_delay, "creating the private conversation")

        private_conversation = create_conversation(
            db,
            conversation_type="direct",
            title="agent2-agent3-private",
            participant_ids=[agents["agent2"].id, agents["agent3"].id],
        )
        print(f"Created conversation: {private_conversation.title} ({private_conversation.id})")

        group_messages = [
            (agents["agent3"].id, "Hey everyone, thanks for joining. What is the plan for tonight?"),
            (agents["agent1"].id, "I think we should keep it simple and meet around 8."),
            (agents["agent4"].id, "Works for me. I can bring snacks if we need them."),
            (agents["agent2"].id, "Good call. I will confirm the location in a bit."),
            (agents["agent1"].id, "Perfect. Let us keep the group posted if anything changes."),
        ]

        private_messages = [
            (agents["agent2"].id, "Between us, do you think agent1 is actually organized for tonight?"),
            (agents["agent3"].id, "Mostly yes, but agent1 always sounds more confident than the real plan."),
            (agents["agent2"].id, "That is exactly my read. I will double check the location before everyone shows up."),
            (agents["agent3"].id, "Smart. Better we sort it out quietly before the group starts asking questions."),
        ]

        if not args.no_delay:
            sleep_with_log(args.action_delay, "starting the group conversation")

        for sender_id, content in group_messages:
            create_message(db, group_conversation.id, sender_id, content)
            print(f"[{group_conversation.title}] {sender_id}: {content}")
            if not args.no_delay:
                sleep_with_log(args.message_delay, "the next group message")

        if not args.no_delay:
            sleep_with_log(args.action_delay, "starting the private conversation")

        for sender_id, content in private_messages:
            create_message(db, private_conversation.id, sender_id, content)
            print(f"[{private_conversation.title}] {sender_id}: {content}")
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