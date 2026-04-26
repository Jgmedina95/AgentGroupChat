from __future__ import annotations

import random


WORD_TO_CLUES = {
	"apple": ["orchard", "pie", "crisp", "cider"],
	"pear": ["green", "soft", "bell", "juicy"],
	"banana": ["yellow", "peel", "smoothie", "tropical"],
	"orange": ["citrus", "zest", "segment", "vitamin"],
}


def choose_clue(word: str, chooser: random.Random) -> str:
	clues = WORD_TO_CLUES.get(word.lower())
	if clues:
		return chooser.choice(clues)
	fallback_tokens = [token for token in word.replace("_", " ").split() if token]
	if fallback_tokens:
		return fallback_tokens[0][:8].lower()
	return "hint"


def build_vote_map(player_names: list[str], impostor_player_name: str) -> dict[str, str]:
	non_impostors = [player_name for player_name in player_names if player_name != impostor_player_name]
	fallback_target = non_impostors[0] if non_impostors else impostor_player_name
	votes = {player_name: impostor_player_name for player_name in non_impostors}
	votes[impostor_player_name] = fallback_target
	return votes
