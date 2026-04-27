from tui.state.store import AgentRecord, AppStore, ConversationRecord


def test_preferred_sender_id_for_conversation_prefers_human_member() -> None:
	store = AppStore()
	store.set_agents(
		[
			AgentRecord(id="assistant-1", type="llm", display_name="Copilot", config={"chat_runtime": "generic_llm_chat"}),
			AgentRecord(id="host-1", type="human", display_name="Jorge", config=None),
		]
	)
	conversation = ConversationRecord(
		id="conversation-1",
		type="direct",
		title="Jorge and Copilot",
		participant_ids=["assistant-1", "host-1"],
	)

	assert store.preferred_sender_id_for_conversation(conversation) == "host-1"


def test_preferred_sender_id_for_conversation_falls_back_to_first_participant() -> None:
	store = AppStore()
	store.set_agents(
		[
			AgentRecord(id="assistant-1", type="llm", display_name="Copilot", config=None),
			AgentRecord(id="assistant-2", type="rule_based", display_name="Bot", config=None),
		]
	)
	conversation = ConversationRecord(
		id="conversation-2",
		type="direct",
		title="Copilot and Bot",
		participant_ids=["assistant-1", "assistant-2"],
	)

	assert store.preferred_sender_id_for_conversation(conversation) == "assistant-1"