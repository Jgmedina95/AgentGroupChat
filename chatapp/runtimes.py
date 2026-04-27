from __future__ import annotations

from typing import Iterable

from chatapp.facade import ChatMember
from simulation.runtimes.llm import LLMPlayerRuntimeFactory


def create_llm_runtime_factory(provider: str | None = None) -> LLMPlayerRuntimeFactory:
	return LLMPlayerRuntimeFactory.from_environment(provider)


def attach_llm_runtime(member: ChatMember, *, factory: LLMPlayerRuntimeFactory):
	runtime = factory.create(
		player_name=member.display_name,
		member_id=member.id,
		gateway=member.server.gateway,
	)
	member.attach_runtime(runtime)
	return runtime


def attach_llm_runtimes(
	members: Iterable[ChatMember],
	*,
	factory: LLMPlayerRuntimeFactory,
) -> dict[str, object]:
	return {member.display_name: attach_llm_runtime(member, factory=factory) for member in members}


__all__ = [
	"attach_llm_runtime",
	"attach_llm_runtimes",
	"create_llm_runtime_factory",
]