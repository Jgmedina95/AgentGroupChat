# Impostor Simulation

## Purpose

This document explains how the current Impostor simulation uses the chat app that already exists today.

The important point is that the game is not implemented as a special database mode. It is implemented on top of normal chat capabilities.

## Current game flow

The current engine runs one round with:

1. one admin member
2. four player members
3. one group conversation for public play
4. one direct conversation between the admin and each player
5. private word assignment
6. public clue posting
7. private vote collection
8. public result announcement

## Which current app capabilities are used

### Member creation

Used to create the admin and each player.

Relevant capability:

- `POST /api/members`

Why it matters:

- the simulation can assign a runtime type like `llm` or `rule_based`
- the chat core still treats them all as normal members

### Member-scoped visibility

Used by each LLM player runtime to inspect only the messages that member is allowed to see.

Relevant capability:

- `GET /api/members/{member_id}/conversations/{conversation_id}/messages`

Why it matters:

- each player reads the game through its own interface layer
- players do not receive global scenario state directly
- this mirrors the eventual goal of per-member context isolation

### Group creation

Used by the admin to create the public play room.

Relevant capability:

- `POST /api/members/{member_id}/conversations/group`

Why it matters:

- the group is still just a normal conversation with memberships
- the scenario uses a standard chat primitive rather than a custom game table

### Direct conversation creation

Used to create one private admin-to-player chat for secret words and votes.

Relevant capability:

- `POST /api/conversations`

Why it matters:

- hidden information already maps naturally onto private chats

### Message posting

Used for:

- rules announcement
- `Ready` acknowledgements
- clue posting
- vote collection
- result announcement

Relevant capabilities:

- `POST /api/members/{member_id}/messages`
- `POST /api/messages`

Why it matters:

- the game loop is implemented using ordinary chat messages

### Pause and resume controls

Used by the admin to freeze group chat while hidden word assignment happens.

Relevant capabilities:

- `POST /api/conversations/{conversation_id}/pause-messages`
- `POST /api/conversations/{conversation_id}/resume-messages`

Why it matters:

- the admin can close the public chat window during secret setup
- this provides the first piece of round-phase control without inventing a full scenario-state service yet

### Capability enforcement

Used implicitly by the chat service when members act.

Why it matters:

- the engine depends on the same rules real clients would face
- simulations do not bypass membership, pause-state, or participant checks

## How the LLM players make decisions

Each player runtime uses:

1. that player's visible group chat transcript
2. that player's private admin chat transcript

From those views, the runtime asks the provider for three decisions:

- `Ready` response
- clue word
- vote target

The current providers are:

- Prime Intellect
- OpenAI

Prime Intellect is the preferred default when `PRIME_API_KEY` is present.

## What is still missing

The current Impostor simulation proves that the chat app can host the scenario, but it is still only the first slice.

Missing pieces include:

- multi-round game state
- tracked elimination across rounds
- automatic win condition handling beyond one round
- richer player personalities and deception strategies
- explicit admin UI controls in the TUI
- reusable scenario state objects independent of the specific Impostor script

## Running the simulation

With the API server running:

```bash
python -m simulation.engine --api-base-url http://127.0.0.1:8000
```

To force Prime Intellect explicitly:

```bash
python -m simulation.engine \
  --api-base-url http://127.0.0.1:8000 \
  --player-runtime llm \
  --llm-provider primeintellect
```