from simulation.core.policies import (
	FirstMatchTerminationPolicy,
	StopCommandTerminationPolicy,
	TerminationDecision,
	UnanimousPreferenceTerminationPolicy,
)


def test_stop_command_policy_returns_empty_decision_when_disabled() -> None:
	decision = StopCommandTerminationPolicy(None).evaluate(
		messages=[{"sender_id": "member-1", "content": "stop"}],
	)

	assert decision == TerminationDecision()


def test_first_match_termination_policy_prefers_stop_over_consensus() -> None:
	policy = FirstMatchTerminationPolicy(
		(
			StopCommandTerminationPolicy("stop"),
			UnanimousPreferenceTerminationPolicy(),
		)
	)

	decision = policy.evaluate(
		messages=[{"sender_id": "member-1", "content": "stop"}],
		preferences={"Nina": "Lisbon", "Marco": "Lisbon"},
	)

	assert decision.stop_requested_by_member_id == "member-1"
	assert decision.consensus_choice is None


def test_first_match_termination_policy_falls_back_to_consensus() -> None:
	policy = FirstMatchTerminationPolicy(
		(
			StopCommandTerminationPolicy("stop"),
			UnanimousPreferenceTerminationPolicy(),
		)
	)

	decision = policy.evaluate(
		messages=[],
		preferences={"Nina": "Lisbon", "Marco": "Lisbon"},
	)

	assert decision.stop_requested_by_member_id is None
	assert decision.consensus_choice == "Lisbon"