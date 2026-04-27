from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class TerminationDecision:
	stop_requested_by_member_id: str | None = None
	consensus_choice: str | None = None

	@property
	def stopped_early(self) -> bool:
		return self.stop_requested_by_member_id is not None


class TurnPolicy(Protocol):
	def order_candidates(self, candidates: list[str]) -> list[str]:
		"""Return the order in which candidates should be offered a turn."""
		...


class TerminationPolicy(Protocol):
	def evaluate(
		self,
		*,
		messages: list[dict[str, Any]],
		preferences: dict[str, str] | None = None,
	) -> TerminationDecision:
		"""Return any stop or consensus condition triggered by the current state."""
		...


@dataclass(slots=True)
class ShuffledTurnPolicy:
	rng: random.Random

	def order_candidates(self, candidates: list[str]) -> list[str]:
		ordered_candidates = list(candidates)
		self.rng.shuffle(ordered_candidates)
		return ordered_candidates


@dataclass(slots=True)
class StopCommandTerminationPolicy:
	stop_command: str | None

	def evaluate(
		self,
		*,
		messages: list[dict[str, Any]],
		preferences: dict[str, str] | None = None,
	) -> TerminationDecision:
		if self.stop_command is None:
			return TerminationDecision()
		normalized_stop_command = self.stop_command.strip().casefold()
		if not normalized_stop_command:
			return TerminationDecision()
		for message in reversed(messages):
			if str(message["content"]).strip().casefold() == normalized_stop_command:
				return TerminationDecision(stop_requested_by_member_id=str(message["sender_id"]))
		return TerminationDecision()


@dataclass(slots=True)
class UnanimousPreferenceTerminationPolicy:
	def evaluate(
		self,
		*,
		messages: list[dict[str, Any]],
		preferences: dict[str, str] | None = None,
	) -> TerminationDecision:
		if not preferences:
			return TerminationDecision()
		if len(set(preferences.values())) != 1:
			return TerminationDecision()
		return TerminationDecision(consensus_choice=next(iter(preferences.values())))


@dataclass(slots=True)
class FirstMatchTerminationPolicy:
	policies: tuple[TerminationPolicy, ...]

	def evaluate(
		self,
		*,
		messages: list[dict[str, Any]],
		preferences: dict[str, str] | None = None,
	) -> TerminationDecision:
		for policy in self.policies:
			decision = policy.evaluate(messages=messages, preferences=preferences)
			if decision.stopped_early or decision.consensus_choice is not None:
				return decision
		return TerminationDecision()