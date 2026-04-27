from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CapabilityOption:
	name: str
	capability_key: str


read_messages = CapabilityOption("read_messages", "read_conversations")
read_message = read_messages
send_messages = CapabilityOption("send_messages", "send_messages")
send_message = send_messages
create_group_chat = CapabilityOption("create_group_chat", "create_group_conversations")
leave_conversations = CapabilityOption("leave_conversations", "leave_conversations")
manage_members = CapabilityOption("manage_members", "manage_memberships")
pause_group_chat = CapabilityOption("pause_group_chat", "pause_group_messages")
resume_group_chat = CapabilityOption("resume_group_chat", "pause_group_messages")


def capabilities_to_payload(
	functionalities: Iterable[CapabilityOption | str] | dict[str, bool] | None,
) -> dict[str, bool] | None:
	if functionalities is None:
		return None
	if isinstance(functionalities, dict):
		return dict(functionalities)

	payload: dict[str, bool] = {}
	for functionality in functionalities:
		if isinstance(functionality, CapabilityOption):
			payload[functionality.capability_key] = True
		elif isinstance(functionality, str):
			payload[functionality] = True
		else:
			raise TypeError(f"Unsupported functionality option: {functionality!r}")
	return payload or None


__all__ = [
	"CapabilityOption",
	"capabilities_to_payload",
	"create_group_chat",
	"leave_conversations",
	"manage_members",
	"pause_group_chat",
	"read_message",
	"read_messages",
	"resume_group_chat",
	"send_message",
	"send_messages",
]