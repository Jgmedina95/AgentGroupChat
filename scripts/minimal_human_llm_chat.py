from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))


import chatapp
from chatapp.live_chat import DEFAULT_ASSISTANT_SYSTEM_PROMPT, GenericLLMChatRuntimeFactory


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Start a minimal direct chat between a human host and one LLM-backed member.")
	parser.add_argument("--api-base-url", default=chatapp.DEFAULT_API_BASE_URL, help="Base URL of the running FastAPI app.")
	parser.add_argument("--mode", choices=["cli", "tui"], default="cli", help="Use stdin for the human side, or keep the assistant alive so you can talk through the TUI.")
	parser.add_argument("--host-name", default="Host")
	parser.add_argument("--assistant-name", default="Assistant")
	parser.add_argument("--title", default=None, help="Optional direct-chat title.")
	parser.add_argument("--llm-provider", choices=["openai", "primeintellect"], help="Optional LLM provider override.")
	parser.add_argument("--system-prompt", default=DEFAULT_ASSISTANT_SYSTEM_PROMPT, help="System prompt used for the LLM member.")
	parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between checks for new host messages in tui mode.")
	return parser.parse_args()


def _print_history(session: chatapp.DirectHumanLLMChatSession) -> None:
	name_by_id = {
		session.host.id: session.host.display_name,
		session.assistant.id: session.assistant.display_name,
	}
	for message in session.conversation.list_messages(viewer=session.host):
		sender_name = name_by_id.get(message["sender_id"], message["sender_id"])
		print(f"{sender_name}> {message['content']}")


def main() -> None:
	args = parse_args()
	server = chatapp.init_server(base_url=args.api_base_url)
	runtime_factory = GenericLLMChatRuntimeFactory.from_environment(
		args.llm_provider,
		system_prompt=args.system_prompt,
	)
	try:
		session = chatapp.create_direct_human_llm_chat(
			server=server,
			runtime_factory=runtime_factory,
			host_name=args.host_name,
			assistant_name=args.assistant_name,
			title=args.title,
		)
		print(f"Host member: {session.host.display_name} ({session.host.id})")
		print(f"LLM member: {session.assistant.display_name} ({session.assistant.id})")
		print(f"Conversation: {session.conversation.title or 'Direct Chat'} ({session.conversation.id})")
		if args.mode == "tui":
			print("TUI mode is active. Open the Textual app, select this conversation, and send messages as the host member.")
			print("Press Ctrl+C here when you want to stop the assistant loop.")
			while True:
				try:
					assistant_message = session.maybe_reply_to_new_host_message()
					if assistant_message is not None:
						print(f"{session.assistant.display_name}> {assistant_message['content']}")
					time.sleep(max(args.poll_interval, 0.1))
				except KeyboardInterrupt:
					print()
					break
			return
		print("Type your message and press Enter. Use /history to print the conversation or /quit to exit.")

		while True:
			try:
				content = input(f"{session.host.display_name}> ").strip()
			except (EOFError, KeyboardInterrupt):
				print()
				break
			if not content:
				continue
			if content == "/quit":
				break
			if content == "/history":
				_print_history(session)
				continue
			session.send_host_message(content)
			assistant_message = session.generate_assistant_reply()
			print(f"{session.assistant.display_name}> {assistant_message['content']}")
	finally:
		runtime_factory.close()
		server.close()


if __name__ == "__main__":
	main()