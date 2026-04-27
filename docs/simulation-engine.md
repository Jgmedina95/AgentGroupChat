# Simulation Engine

## Purpose

The simulation engine is a client of the chat app. It should decide when members act, but it should not write directly to sqlite or bypass the API rules.

The current implementation lives under `simulation/` and is intentionally small.

## Main files

- `simulation/engine.py`: orchestration entry point and CLI.
- `simulation/runtimes/llm.py`: LLM-backed player interface and provider selection.
- `simulation/runtimes/rule_based.py`: deterministic fallback behavior for local runs and tests.
- `simulation/trip_planner.py`: friends trip scenario orchestration and CLI.
- `simulation/runtimes/trip_planner.py`: persona-driven trip-planner runtime and scripted test client.

## Key pieces in `simulation/engine.py`

### `ImpostorGameConfig`

Defines the run configuration for one Impostor round.

Important fields:

- `player_names`
- `shared_word`
- `impostor_word`
- `impostor_player_name`
- `player_runtime_type`
- `llm_provider`
- `action_delay_seconds`

### `RestChatGateway`

Wraps the chat app HTTP surface so the simulation engine uses the same contract as any other client.

It is responsible for:

- creating members
- creating group and direct conversations
- posting member messages
- pausing and resuming group chats
- reading visible messages for a specific member

This is the main abstraction boundary between scenario logic and chat infrastructure.

### `ImpostorSimulationEngine`

Runs one scripted round of the game.

Current responsibilities:

1. create one admin and four players
2. create one group chat and one private chat per player
3. publish rules in the group chat
4. collect `Ready` from each player
5. pause the group chat while secret words are assigned privately
6. resume the group chat and ask each player for a clue
7. collect private votes
8. publish the result in the group chat

The engine now also cleans up any internally created LLM runtime client factory through `close()`.

## Key pieces in `simulation/trip_planner.py`

### `FriendsTripConfig`

Defines the run configuration for the friends trip scenario.

Important fields:

- `friends`
- `initiator_name`
- `destination_options`
- `max_discussion_rounds`
- `discussion_seed`
- `stop_command`
- `continue_until_stopped`
- `action_delay_seconds`

### `FriendsTripSimulationEngine`

Runs a group of friends through a destination-planning conversation using the same member-scoped chat surface as the rest of the project.

Current responsibilities:

1. create one admin and one member per friend persona
2. create one shared group chat and one private brief chat per friend
3. seed each friend with a private planning brief
4. let the initiator start the discussion in the group chat
5. offer speaking opportunities each round while allowing friends to stay quiet and wait for more context
6. collect each friend's current preference after a round
7. either auto-finish with a host conclusion or keep running until the group reaches consensus, someone posts the configured stop command, or the discussion stalls

When `continue_until_stopped=True`, the engine behaves like a live chat client: the discussion can continue past the nominal round limit and ends only when the configured stop message appears in the group conversation.

When `continue_until_stopped=False`, the trip planner is also no longer bounded by a fixed round count for the final outcome. It keeps discussing until the group reaches consensus or the conversation stalls with no new messages, at which point the host concludes with the no-trip outcome.

## Runtime model

### Rule-based runtime

The rule-based runtime is deterministic and used for:

- fast local smoke runs
- tests
- fallback behavior when LLM use is not desired

### LLM runtime

The LLM runtime gives each player its own interface layer.

That means each player sees only:

- the visible group conversation history available to that member
- that player's private chat with the admin

From that view, the LLM decides:

- whether to send `Ready`
- which clue word to send
- which player to vote for

The engine does not hand the LLM global scenario state directly. It uses the same member-scoped message visibility the chat app exposes.

## Provider selection

Provider resolution lives in `simulation/runtimes/llm.py`.

Supported providers:

- `primeintellect`
- `openai`

Selection order:

1. explicit `--llm-provider`
2. `AGENT_CHAT_LLM_PROVIDER`
3. automatic preference for Prime Intellect if `PRIME_API_KEY` is present
4. OpenAI if Prime is not configured and `OPENAI_API_KEY` or `AGENT_CHAT_LLM_API_KEY` is present

### Prime Intellect settings

- `PRIME_API_KEY`
- `PRIME_TEAM_ID` or `AGENT_CHAT_PRIME_TEAM_ID`
- `AGENT_CHAT_PRIME_MODEL` to override the default model
- `AGENT_CHAT_PRIME_BASE_URL` to override the default base URL

Current default Prime model:

- `meta-llama/llama-3.3-70b-instruct`

### OpenAI settings

- `OPENAI_API_KEY` or `AGENT_CHAT_LLM_API_KEY`
- `AGENT_CHAT_OPENAI_MODEL`
- `AGENT_CHAT_OPENAI_BASE_URL`

## CLI usage

Run a simulation against the live server:

```bash
python -m simulation.engine --api-base-url http://127.0.0.1:8000
```

Run the friends trip planner against the live server:

```bash
.venv/bin/python -m simulation.trip_planner --api-base-url http://127.0.0.1:8000
```

The trip planner uses live LLM-backed friends by default and pauses between actions so the TUI can follow the conversation. Use `--no-delay` when you want the scenario to complete immediately.

Important trip planner flags:

- `--stop-command` to change the exact message that ends a live run
- `--auto-finish` to let the host conclude the run automatically when the discussion stalls instead of waiting for an explicit stop message
- `--discussion-seed` for reproducible turn-taking
- `--max-rounds` is now a legacy compatibility option and no longer bounds the outcome directly

Force rule-based players:

```bash
python -m simulation.engine --api-base-url http://127.0.0.1:8000 --player-runtime rule_based
```

Force Prime Intellect explicitly:

```bash
python -m simulation.engine \
  --api-base-url http://127.0.0.1:8000 \
  --player-runtime llm \
  --llm-provider primeintellect
```

## Current limitations

- no alive or eliminated roster is persisted across rounds
- generic scenario abstractions are still incomplete even though the trip planner now has explicit simulation state
- the admin is scripted, not LLM-backed
- votes are collected through private direct messages, not poll primitives
- the notebook trip demo is still synchronous, so truly interactive stop-control is cleaner through the live server or TUI than through the in-process notebook flow