from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
	return datetime.now(timezone.utc)


@dataclass(slots=True)
class SimulationTraceEvent:
	event_type: str
	recorded_at: datetime = field(default_factory=_utc_now)
	round_index: int | None = None
	member_id: str | None = None
	member_name: str | None = None
	conversation_id: str | None = None
	details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SimulationTraceRecorder:
	events: list[SimulationTraceEvent] = field(default_factory=list)

	def record(
		self,
		*,
		event_type: str,
		round_index: int | None = None,
		member_id: str | None = None,
		member_name: str | None = None,
		conversation_id: str | None = None,
		details: dict[str, Any] | None = None,
	) -> SimulationTraceEvent:
		event = SimulationTraceEvent(
			event_type=event_type,
			round_index=round_index,
			member_id=member_id,
			member_name=member_name,
			conversation_id=conversation_id,
			details={} if details is None else dict(details),
		)
		self.events.append(event)
		return event


def format_trace_event(event: SimulationTraceEvent) -> str:
	round_prefix = "" if event.round_index is None else f"Round {event.round_index + 1}: "
	member_name = event.member_name or "Unknown member"

	if event.event_type == "group_chat_created":
		title = str(event.details.get("title", "Untitled chat"))
		return f"[event] {member_name} created group chat: {title}"

	if event.event_type == "private_chat_created":
		peer_name = str(event.details.get("peer_name", "unknown member"))
		return f"[event] {member_name} created private chat with {peer_name}"

	if event.event_type == "turn_candidates_ordered":
		candidate_names = event.details.get("candidate_names", [])
		formatted_candidates = ", ".join(str(name) for name in candidate_names) or "none"
		return f"[event] {round_prefix}candidate order: {formatted_candidates}"

	if event.event_type == "turn_offered":
		return f"[event] {round_prefix}offered turn to {member_name}"

	if event.event_type == "turn_skipped":
		return f"[event] {round_prefix}{member_name} decided not to answer"

	if event.event_type == "message_posted":
		content = str(event.details.get("content", ""))
		message_scope = str(event.details.get("message_scope", "message"))
		recipient_name = event.details.get("recipient_name")
		if message_scope == "private" and recipient_name is not None:
			return f"[event] {round_prefix}{member_name} sent a private message to {recipient_name}: \"{content}\""
		return f"[event] {round_prefix}{member_name} sent a message: \"{content}\""

	if event.event_type == "consensus_checked":
		consensus_choice = event.details.get("consensus_choice")
		if consensus_choice is not None:
			return f"[event] {round_prefix}consensus reached on {consensus_choice}"
		return f"[event] {round_prefix}consensus check found split preferences"

	if event.event_type == "stop_requested":
		return f"[event] {round_prefix}{member_name} requested the simulation to stop"

	return f"[event] {round_prefix}{event.event_type}"


def render_trace_log(events: list[SimulationTraceEvent]) -> str:
	return "\n".join(format_trace_event(event) for event in events)


def write_trace_log(events: list[SimulationTraceEvent], output_path: str | Path) -> Path:
	path = Path(output_path)
	path.write_text(render_trace_log(events) + "\n", encoding="utf-8")
	return path