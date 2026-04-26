# Simulation Engine

## Purpose

The simulation engine is a client of the chat app. It should decide when members act, but it should not write directly to sqlite or bypass the API rules.

The current implementation lives under `simulation/` and is intentionally small.

## Main files

- `simulation/engine.py`: orchestration entry point and CLI.
- `simulation/runtimes/llm.py`: LLM-backed player interface and provider selection.
- `simulation/runtimes/rule_based.py`: deterministic fallback behavior for local runs and tests.

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

- only one round is orchestrated today
- no alive or eliminated roster is persisted across rounds
- there is no generic scenario state object yet beyond the Impostor config and result
- the admin is scripted, not LLM-backed
- votes are collected through private direct messages, not poll primitives