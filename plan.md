# Refactor Plan

## Current Simulation Architecture Plan

### Direction

Keep the chat substrate as the source of truth and make the simulation side more composable.

This means:

- conversation, membership, visibility, and message posting stay owned by the chat layer
- simulations remain clients of that chat layer instead of bypassing it
- orchestration becomes modular through reusable policies rather than being hard-coded per scenario

### Immediate phases

1. Extract orchestration policies from scenarios.
Start with `TurnPolicy` and `TerminationPolicy` in the trip planner so turn-taking and stop or consensus checks are reusable without changing chat behavior.

2. Add simulation trace events.
Record decisions such as offered turns, skipped turns, posted messages, consensus checks, and stop triggers so the TUI and future replay tools can inspect why the simulation moved the way it did.

3. Introduce explicit scenario state objects.
Move mutable scenario data out of engine loops into serializable state structures.

4. Add declarative scenario specs.
Support scenario configuration for personas, policies, pacing, and termination rules without rewriting orchestration code.

### Step 1 scope

The first implementation step should be intentionally small:

- add reusable policy classes under `simulation/core/`
- move trip planner turn ordering behind a `TurnPolicy`
- move stop and consensus checks behind `TerminationPolicy` implementations
- keep the current trip-planner behavior and tests unchanged

## Goal

Turn this repository into a reusable chat platform where:

- chat is the stable core domain
- admin is one client of that domain, not the domain itself
- human members, LLM agents, and scripted agents can all participate through the same chat contract
- simulation and runtime behavior are layered on top of chat instead of being baked into CRUD routes

This plan assumes the current repo stays as one repository for now.

## Core design decision

Do not split by app first. Split by responsibility first.

The current codebase already has useful separation in these areas:

- API surface in `api/`
- persistence in `db/` and `models/`
- chat logic in `services/message_service.py`
- admin clients in `tui/` and `websocket_viewer.html`
- future runtime surface in `simulation/`

What is missing is a clean domain boundary. Right now the backend mixes:

- chat platform concepts
- admin workflows
- future simulation concerns

The refactor should create one clear center: chat core.

## Target architecture

```text
clients/
    admin_tui/
    browser_admin/

platform/
    chat/
        domain/
        application/
        infrastructure/
        api/
        realtime/

simulation/
    engine/
    runtimes/
    policies/
```

In practical terms for this repo, that means:

- chat rules become the source of truth
- API routes become thin wrappers over chat use cases
- admin UI consumes the same API and events as every other client
- simulation code depends on chat use cases and events, not direct table mutation

## Phase 1: Lock the language

Before moving files, define the domain vocabulary and use it consistently.

### Recommended terms

- `Member`: identity that can belong to conversations and send messages
- `Runtime`: execution strategy behind a member, such as `human`, `llm`, or `rule_based`
- `Conversation`: chat container, either direct or group
- `Membership`: relationship between member and conversation, with status and role
- `Message`: immutable chat event with optional soft-delete marker

### What to change

- Treat current `Agent` as an identity model that will likely be renamed to `Member`
- Keep `type` or similar runtime information, but move the meaning toward runtime classification rather than identity classification
- Stop designing new behavior around the word `Agent` unless you are sure human users will never be first-class participants

### Deliverable

Write a short domain glossary in the README and mirror the same terms in code comments, route names, and tests.

## Phase 2: Fix the domain model before adding features

This is the most important step.

The current conversation model supports a fixed participant list at creation time. Your new product direction requires membership lifecycle.

### Current gap

The existing model in `models/chat.py` and `services/message_service.py` can:

- create a conversation with participants
- check whether a sender belongs to a conversation
- list messages

It cannot represent:

- invites
- join requests
- accepted versus pending membership
- member roles
- leaving a conversation
- conversation ownership
- open versus restricted groups

### Model changes

Expand conversation membership from a simple join table into a real entity.

Recommended fields:

- `conversation_id`
- `member_id`
- `status`: `invited | active | left | rejected`
- `role`: `owner | member | moderator`
- `invited_by_member_id`
- `joined_at`
- `left_at`

Recommended conversation fields:

- `created_by_member_id`
- `join_policy`: `invite_only | open | approval_required`
- `status`: `active | archived`

### Deliverable

Create the database changes and update the SQLAlchemy models without changing the UI yet.

## Phase 3: Move from CRUD services to use cases

The current service layer is centered on low-level create and list functions. That will become brittle as rules expand.

### Replace generic operations with behavior-specific use cases

Introduce an application layer with use cases such as:

- `register_member`
- `start_direct_conversation`
- `create_group_conversation`
- `invite_member`
- `join_conversation`
- `leave_conversation`
- `post_message`
- `delete_message`
- `archive_conversation`

### Rules that should live there

- only active members can post
- direct conversations should have deterministic uniqueness rules if desired
- open groups can be joined without invite
- invite-only groups require active invitation
- leaving a group should not delete the conversation
- deleting a conversation should probably become archive behavior for most cases

### Deliverable

Create a new application module and route existing API handlers through those use cases.

## Phase 4: Separate platform code from admin code

Once the domain and use cases exist, split the codebase by ownership.

### Suggested directory direction

```text
api/
    admin_routes.py
    chat_routes.py
    websockets.py

chat/
    domain/
        models.py
        enums.py
    application/
        commands.py
        services.py
    infrastructure/
        repositories.py
        persistence.py

admin/
    tui/
    browser/

simulation/
    engine.py
    runtimes/
```

You do not need to land this exact shape in one commit. The important boundary is:

- chat core contains no TUI or viewer assumptions
- admin code contains no direct database logic
- simulation code does not call SQLAlchemy models directly

### Deliverable

Move the current TUI and browser viewer mentally and structurally under an `admin` surface.

## Phase 5: Redesign the API around the product direction

The current routes are enough for a smoke test but not for the product you described.

### Keep

- list conversations
- list messages
- websocket event streams

### Add

- create member
- create direct conversation
- create group conversation
- invite member to conversation
- accept invite
- decline invite
- join open conversation
- leave conversation
- list conversation members with statuses and roles

### Likely reshape

- `POST /api/conversations` becomes more explicit or gains stricter validation
- membership actions should have their own endpoints instead of being implicit in conversation creation
- deletion may become archive or soft-delete depending on intended admin semantics

### Deliverable

Write request and response schemas for membership lifecycle before implementing all endpoints.

## Phase 6: Introduce chat events as the integration contract

This is the cleanest boundary between chat core, admin clients, and simulation.

### Event examples

- `conversation.created`
- `membership.invited`
- `membership.joined`
- `membership.left`
- `message.created`
- `message.deleted`
- `conversation.archived`

### Why this matters

- the admin TUI can update from the same event stream
- future member clients can react without custom coupling
- the simulation engine can subscribe to chat activity without owning storage

### Deliverable

Create an internal event publisher abstraction before the simulation engine starts doing real work.

## Phase 7: Keep simulation downstream of chat core

The empty `simulation/engine.py` is an advantage right now. Keep it that way until chat boundaries are stable.

### Rules for simulation code

- simulation decides when a runtime wants to act
- simulation calls chat use cases to perform actions
- simulation never inserts rows directly
- runtime-specific context and tool access stay outside the chat core

### Good first interface

```python
class ChatGateway:
        async def post_message(self, conversation_id: str, sender_id: str, content: str) -> None: ...
        async def list_active_members(self, conversation_id: str) -> list[str]: ...
```

### Deliverable

Define the gateway interface and keep the actual engine minimal until membership workflows exist.

## Phase 8: Expand tests before deeper refactors

The current tests are good smoke coverage, but they only verify a narrow happy path.

Add tests for:

- direct conversation creation rules
- group conversation creation rules
- membership validation on message posting
- invite acceptance and rejection
- join and leave behavior
- websocket emission for membership events
- archived conversation behavior

Testing order matters:

1. add tests for the intended domain behavior
2. refactor behind those tests
3. migrate the UI clients afterward

## Phase 9: Defer the bigger infrastructure moves

Do not do these yet:

- multiple repositories
- microservices
- Redis or Kafka
- background workers for everything
- PostgreSQL migration purely for architecture aesthetics

You may need some of these later, but today they would slow down the more important work of clarifying the domain.

## Recommended execution order

### Milestone 1: Domain stabilization

- define glossary
- rename or alias `Agent` toward `Member`
- add membership state and roles
- add conversation ownership and join policy

### Milestone 2: Application layer

- create use-case oriented service layer
- update routes to call use cases
- keep current response shapes working where possible

### Milestone 3: Admin isolation

- move TUI and viewer under an admin surface
- keep them as clients of the chat API only

### Milestone 4: Simulation foundation

- define chat gateway interface
- start publishing internal chat events
- implement first runtime against the gateway

Concrete target for this milestone:

- run a full social-deduction scenario like `Impostor` through the chat substrate, with one admin member, four player members, one shared group chat, private admin-to-player chats, round-based turn control, hidden word assignment, readiness handling, pause and resume controls, and private vote collection

### Milestone 5: Product expansion

- member-initiated chats
- group invites and optional joins
- richer moderation and policy rules

## First concrete tasks

If you want to start immediately, the best next tasks are:

1. Replace the current participant join table with a real membership model.
2. Add use cases for `create_group_conversation`, `invite_member`, `join_conversation`, and `leave_conversation`.
3. Add tests for membership states before touching the TUI.
4. Rename the TUI mentally and structurally as the admin client.
5. Leave the simulation engine mostly empty until the chat contract is stable.

## Immediate next implementation slice

The next three steps should focus on turning the current chat substrate into a member-managed group system. This is the minimum useful slice before deeper LLM simulation work.

### Step 1: Member-managed group actions

Goal:

- make group lifecycle actions happen from the perspective of a member, not an admin-only client

Scope:

- add `created_by_member_id` as the required actor for group creation behavior
- add use cases for `create_group_conversation`, `add_member_to_conversation`, `remove_member_from_conversation`, and `leave_conversation`
- keep direct conversations separate from group-management rules to avoid overloading one endpoint
- preserve current message send/delete behavior while adding membership mutation actions

Suggested API shape for this slice:

- `POST /api/conversations/group`
- `POST /api/conversations/{conversation_id}/members`
- `DELETE /api/conversations/{conversation_id}/members/{member_id}`
- `POST /api/conversations/{conversation_id}/leave`

Exit criteria:

- a member can create a group
- the acting member becomes the owner
- the owner can add or remove members
- a member can leave without deleting the conversation

### Step 2: Permission rules and membership policy

Goal:

- make membership roles and statuses control behavior instead of existing only as stored fields

Scope:

- treat `active`, `invited`, `left`, and `removed` as behaviorally meaningful states
- enforce that only active members can post messages
- enforce that only owners or moderators can add or remove other members
- allow members to remove themselves via leave behavior without needing owner permission
- decide what should happen when the last owner leaves: transfer ownership, block the action, or archive the group
- decide whether removed members stay visible in history or disappear from active membership views

Implementation preference:

- keep these checks in one policy layer or application service instead of scattering them across route handlers

Exit criteria:

- forbidden actions fail consistently with clear errors
- membership status changes affect posting and management behavior immediately
- role-based rules are explicit enough to support later LLM simulation scenarios

### Future capability: Polls and structured votes

Do not implement this in the current slice, but keep it on the roadmap as an optional conversation capability.

Scope for later:

- allow an authorized member type to post a poll into a conversation
- allow eligible members to cast one vote per poll
- expose poll creation and voting as capabilities that can be enabled or disabled by scenario
- support game-style private votes and WhatsApp-style group polls without coupling either flow to the base message model

Why later:

- pause/resume and message-window controls are the smaller prerequisite for round-based games
- the first useful vote system should be capability-driven rather than hardcoded to one scenario

## Target scenario: Impostor

This is the first concrete simulation the platform should be able to run end to end.

Desired flow:

1. Create five members: four players and one admin.
2. Admin creates one group chat with all players and one private direct conversation with each player.
3. Admin posts the game rules in the group chat and waits for each player to send `Ready`.
4. Once all players are ready, admin pauses group-chat posting.
5. Admin privately assigns hidden words so that three players share one word and one player gets a different word.
6. Admin resumes group-chat posting and controls turn order so each player posts one related clue.
7. After all clues are posted, admin collects votes privately from each player.
8. Admin announces the vote results in the group chat.
9. If the impostor is eliminated, end the game. Otherwise continue into the next round until the configured win condition is reached.

What the chat platform already supports for this:

- members with capability overrides
- admin-controlled pause and resume for group messages
- group and direct conversations
- private and shared message history retrieval
- member-scoped actions and visibility APIs

What is still missing before this becomes a real simulation run instead of manual orchestration:

- a scenario state model for rounds, readiness, alive or eliminated players, hidden assignments, and win conditions
- a simulation engine that decides when each member should act and calls the chat API or gateway
- member decision policies for waiting, responding, clue generation, and voting
- a structured voting flow, whether implemented as private direct-message collection or later as poll primitives
- admin orchestration helpers for detecting readiness completion, assigning turn order, and advancing rounds

Recommended next slice for this target:

- define a `simulation/engine.py` contract around a chat gateway
- model one `ImpostorGameState` object outside the chat core
- implement a minimal admin orchestrator that uses existing chat endpoints to run one round
- keep voting in direct conversations first, without adding poll infrastructure yet

### Step 3: Tests before wider UI work

Goal:

- lock the intended behavior before adding more TUI or simulation complexity

Required tests for this slice:

- owner creates a group and becomes owner
- owner adds a member to a group
- non-owner cannot add a member
- owner removes a member from a group
- removed or left members cannot post
- active members can still post
- member can leave a group without deleting it
- websocket events fire for member added, member removed, and member left

Recommended order:

1. write the failing tests
2. implement the use cases and policy rules
3. expose the endpoints
4. update the TUI only after the backend behavior is stable

Exit criteria:

- the behavior is enforced by tests rather than by manual verification in the TUI
- future simulation runtimes can rely on the same contract the admin client uses

## Definition of success

This refactor is successful when:

- chat rules live in one reusable core
- admin becomes just one client of that core
- members can create, join, leave, and message without special-case hacks
- future LLM or scripted runtimes can participate through the same contract
- simulation can evolve independently from persistence and UI details

## Task-by-task checklist

This checklist is ordered to minimize churn. Each task should land in a small commit or small batch of related commits.

### Stage 0: Naming and decision lock

- [ ] Write a short glossary section in `readme.md` for `Member`, `Runtime`, `Conversation`, `Membership`, and `Message`.
- [ ] Decide whether the database table stays named `agents` temporarily or is renamed immediately to `members`.
- [ ] Decide whether direct conversations are unique per member pair or whether duplicates are allowed.
- [ ] Decide whether deleting a conversation means `archive` or hard delete going forward.
- [ ] Decide whether open groups are discoverable by default or joinable only via explicit ID.

Exit criteria:

- all five decisions are written down in docs before model changes start

### Stage 1: Membership model foundation

- [ ] Add enums or constrained string values for membership status, membership role, conversation status, and join policy.
- [ ] Expand the current conversation participant model into a real membership entity.
- [ ] Add `created_by_member_id` to conversations.
- [ ] Add `join_policy` and `status` to conversations.
- [ ] Add invitation and membership lifecycle timestamps.
- [ ] Update SQLAlchemy relationships in the current model layer.
- [ ] Keep existing list and read flows working with compatibility shims where possible.

Files likely touched first:

- `models/chat.py`
- `models/__init__.py`
- `db/base.py`

Exit criteria:

- the schema can represent invited, active, left, and rejected members without UI changes

### Stage 2: Behavior tests before route refactor

- [ ] Add tests for creating a direct conversation.
- [ ] Add tests for creating a group conversation.
- [ ] Add tests for posting as an active member.
- [ ] Add tests for owner-managed group creation and ownership assignment.
- [ ] Add tests for owner add-member behavior.
- [ ] Add tests that non-owners cannot add members.
- [ ] Add tests for owner remove-member behavior.
- [ ] Add tests rejecting posting by invited or non-member users.
- [ ] Add tests rejecting posting by left or removed members.
- [ ] Add tests for invite acceptance.
- [ ] Add tests for leaving a conversation.
- [ ] Add tests for archived conversation restrictions.
- [ ] Add websocket tests for membership added, removed, and left events.

Files likely touched first:

- `tests/test_api.py`
- new test helpers if needed under `tests/`

Exit criteria:

- tests describe the target behavior even if internals still need refactoring

### Stage 3: Extract chat_core application layer

- [ ] Create a `chat_core` package.
- [ ] Move business rules out of `services/message_service.py` into application use cases.
- [ ] Introduce use cases for `register_member`, `start_direct_conversation`, `create_group_conversation`, `add_member_to_conversation`, `remove_member_from_conversation`, `invite_member`, `join_conversation`, `leave_conversation`, `post_message`, `delete_message`, and `archive_conversation`.
- [ ] Introduce a policy layer for role-based membership and posting permissions.
- [ ] Keep route handlers thin and focused on HTTP validation plus serialization.
- [ ] Introduce DTOs or read models for API responses.
- [ ] Preserve current endpoint behavior where backward compatibility is cheap.

Files likely created:

- `chat_core/application/`
- `chat_core/domain/`
- `chat_core/infrastructure/`

Files likely reduced in scope:

- `services/message_service.py`
- `api/routes.py`

Exit criteria:

- route handlers no longer directly contain business workflow decisions

### Stage 4: Event contract and realtime cleanup

- [ ] Define internal event objects for conversation, membership, and message lifecycle changes.
- [ ] Add an event publisher abstraction inside `chat_core`.
- [ ] Refactor websocket broadcasting so HTTP handlers are not the primary owners of event construction.
- [ ] Add support for `membership.invited`, `membership.joined`, and `membership.left` events.
- [ ] Keep existing conversation and message websocket events functioning.

Files likely touched:

- `api/websockets.py`
- `api/routes.py`
- new event modules under `chat_core/`

Exit criteria:

- chat events are emitted from application outcomes rather than assembled ad hoc in route functions

### Stage 5: API redesign for membership lifecycle

- [ ] Split route concerns between member, conversation, membership, and message actions.
- [ ] Add explicit membership endpoints.
- [ ] Add conversation member listing with roles and statuses.
- [ ] Add actor-aware group management endpoints for add, remove, and leave behavior.
- [ ] Decide whether to keep one conversations endpoint or split direct and group creation flows.
- [ ] Update response models to expose membership lifecycle state cleanly.

Suggested endpoint targets:

- `POST /api/members`
- `GET /api/members`
- `POST /api/conversations/direct`
- `POST /api/conversations/group`
- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}/members`
- `POST /api/conversations/{conversation_id}/members`
- `DELETE /api/conversations/{conversation_id}/members/{member_id}`
- `POST /api/conversations/{conversation_id}/invites`
- `POST /api/conversations/{conversation_id}/join`
- `POST /api/conversations/{conversation_id}/leave`
- `POST /api/messages`
- `GET /api/conversations/{conversation_id}/messages`

Exit criteria:

- the HTTP API expresses the domain actions directly instead of relying on overloaded payloads

### Stage 6: admin_app isolation

- [ ] Create an `admin_app` package or top-level folder.
- [ ] Move the Textual app under `admin_app/tui/`.
- [ ] Move browser viewer assets under `admin_app/browser/`.
- [ ] Add an admin-oriented API client layer that depends only on HTTP and websocket contracts.
- [ ] Remove any assumptions in admin code that reach into ORM models or backend internals.
- [ ] Rename UI labels from generic chat wording to admin wording where useful.

Current code likely moved:

- `tui/`
- `websocket_viewer.html`

Exit criteria:

- admin is structurally and conceptually a client of `chat_core`

### Stage 7: simulation boundary setup

- [ ] Define a `ChatGateway` interface for simulation use.
- [ ] Define runtime interfaces for human, rule-based, and LLM participants.
- [ ] Ensure simulation can react to chat events without direct ORM access.
- [ ] Add one minimal runtime path that posts through chat use cases.
- [ ] Do not add complex orchestration until membership lifecycle is stable.

Files likely touched:

- `simulation/engine.py`
- `simulation/runtimes/`
- new gateway adapter in `chat_core`

Exit criteria:

- simulation has a stable integration seam without becoming responsible for chat persistence

### Stage 8: cleanup and migration pass

- [ ] Remove or deprecate old service functions that duplicate application-layer behavior.
- [ ] Rename files and imports from `Agent` to `Member` where the terminology is now settled.
- [ ] Update seed scripts to use the new API behavior.
- [ ] Update `readme.md` run examples and endpoint examples.
- [ ] Remove compatibility shims only after the tests and admin client pass.

Exit criteria:

- old and new paths are no longer competing for ownership of the same logic

## Final module design draft

This is the target module shape after the refactor settles. It is a draft, not a requirement to move every file at once.

### `chat_core`

`chat_core` is the platform module. It owns the domain, business rules, event production, and infrastructure adapters needed to persist and expose chat behavior.

#### Responsibilities

- define the chat domain model
- enforce conversation and membership rules
- expose application use cases
- publish chat lifecycle events
- provide repository and persistence adapters
- provide HTTP and websocket-facing serializers or contracts

#### Target structure

```text
chat_core/
    __init__.py
    domain/
        __init__.py
        enums.py
        entities.py
        policies.py
        events.py
        errors.py
    application/
        __init__.py
        commands.py
        dto.py
        services/
            member_service.py
            conversation_service.py
            membership_service.py
            message_service.py
        interfaces.py
    infrastructure/
        __init__.py
        orm_models.py
        repositories.py
        sqlalchemy_repositories.py
        event_bus.py
    api/
        __init__.py
        schemas.py
        serializers.py
        routes_members.py
        routes_conversations.py
        routes_memberships.py
        routes_messages.py
    realtime/
        __init__.py
        connection_manager.py
        event_mapper.py
        websocket_routes.py
```

#### Internal design notes

`chat_core.domain.enums.py`

- `ConversationType`
- `ConversationStatus`
- `MembershipStatus`
- `MembershipRole`
- `JoinPolicy`
- `MemberRuntimeType`

`chat_core.domain.entities.py`

- `Member`
- `Conversation`
- `Membership`
- `Message`

These can begin as ORM-backed models if you want a lighter refactor, but the responsibility boundary should still be clear: business meaning belongs here, not in route functions.

`chat_core.domain.policies.py`

- `can_member_post_message`
- `can_member_join_conversation`
- `can_member_invite_others`
- `can_member_leave_conversation`
- `should_archive_empty_group`

`chat_core.domain.events.py`

- `ConversationCreated`
- `MembershipInvited`
- `MembershipJoined`
- `MembershipLeft`
- `MessageCreated`
- `MessageDeleted`
- `ConversationArchived`

`chat_core.application.commands.py`

- request-style command objects or typed payloads for each use case

`chat_core.application.dto.py`

- `MemberView`
- `ConversationView`
- `MembershipView`
- `MessageView`

`chat_core.application.services/`

- `member_service.py`: registration and member lookup behavior
- `conversation_service.py`: direct/group creation, archive, list
- `membership_service.py`: invite, accept, reject, join, leave
- `message_service.py`: post, soft-delete, list

`chat_core.application.interfaces.py`

- repository protocols
- event publisher protocol
- optional clock and ID generator abstractions if you want cleaner tests

`chat_core.infrastructure.orm_models.py`

- SQLAlchemy mappings if you choose to separate ORM definitions from pure domain types

`chat_core.infrastructure.repositories.py`

- repository interfaces backed by SQLAlchemy implementations

`chat_core.infrastructure.sqlalchemy_repositories.py`

- concrete persistence logic, query composition, and transaction boundaries

`chat_core.infrastructure.event_bus.py`

- in-process publisher for now
- easy to replace later with Redis, Kafka, or background jobs if needed

`chat_core.api.schemas.py`

- Pydantic request and response schemas

`chat_core.api.serializers.py`

- mapping from application DTOs to API payloads

`chat_core.realtime.event_mapper.py`

- transforms internal events into websocket event envelopes

#### Public use cases expected from `chat_core`

- `register_member`
- `list_members`
- `start_direct_conversation`
- `create_group_conversation`
- `list_conversations`
- `list_conversation_members`
- `invite_member`
- `accept_invite`
- `reject_invite`
- `join_conversation`
- `leave_conversation`
- `post_message`
- `list_messages`
- `delete_message`
- `archive_conversation`

#### Data and control boundaries

- no Textual imports
- no browser-specific code
- no LLM SDK dependencies
- no simulation rules
- no direct UI formatting logic

### `admin_app`

`admin_app` is the operator-facing client package. It should help inspect, manage, seed, and observe the chat system, but it should not own chat rules.

#### Responsibilities

- present live conversation state for operators
- allow admin actions through public APIs
- subscribe to websocket streams for observation
- provide lightweight tooling for demos, resets, and inspection
- remain replaceable without forcing backend changes

#### Target structure

```text
admin_app/
    __init__.py
    tui/
        __init__.py
        __main__.py
        app.py
        components/
            conversation_list.py
            member_panel.py
            membership_panel.py
            message_panel.py
            event_log.py
        services/
            api_client.py
            websocket_client.py
        state/
            store.py
            selectors.py
    browser/
        websocket_viewer.html
        static/
        api_debug_views/
    tools/
        reset_data.py
        seed_demo_data.py
```

#### TUI design draft

Primary screens or panels:

- conversation list
- selected conversation detail
- membership roster with roles and statuses
- message stream
- system event log
- admin action panel for invite, join, leave, archive, and send message

State model in `admin_app.tui.state.store.py`:

- known members
- known conversations
- memberships by conversation
- messages by conversation
- recent events
- selected conversation ID
- connection status

API client responsibilities in `admin_app.tui.services.api_client.py`:

- list members
- list conversations
- list conversation members
- list messages
- create direct conversation
- create group conversation
- invite member
- join conversation
- leave conversation
- send message
- archive conversation

Websocket client responsibilities in `admin_app.tui.services.websocket_client.py`:

- subscribe to all-conversation event stream
- subscribe to per-conversation event stream
- normalize incoming envelopes
- reconnect with backoff
- expose status updates to the store

#### Browser admin draft

Keep the browser side narrow at first:

- websocket event inspector
- simple conversation browser
- optional membership inspection view

Do not turn this into a second full frontend until the chat contract stabilizes.

#### Data and control boundaries

- no direct SQLAlchemy access
- no imports from low-level backend modules
- all actions go through HTTP or websocket contracts
- UI state is derived from API responses and event streams

### `simulation`

`simulation` is the orchestration layer for non-admin participants and scenario logic. It should stay deliberately thinner than `chat_core` until the platform contract stabilizes.

#### Responsibilities

- host runtimes that decide when and how members act
- translate scenario logic into chat actions
- react to chat events
- manage turn-taking, timing, or scheduling when needed

#### Target structure

```text
simulation/
    __init__.py
    engine.py
    gateway.py
    runtimes/
        __init__.py
        base.py
        human_proxy.py
        rule_based.py
        llm.py
    policies/
        __init__.py
        base.py
        market.py
        collaboration.py
    state/
        __init__.py
        run_context.py
```

#### Design notes

`simulation.gateway.py`

- interface used to call `chat_core` behaviors
- no direct DB access

`simulation.engine.py`

- subscribes to chat events
- decides whether a runtime should react
- schedules or executes follow-up actions

`simulation.runtimes.base.py`

- common runtime interface like `decide_actions(context) -> list[Action]`

`simulation.policies/`

- scenario-specific rules that are not universal chat rules

#### Boundaries

- no ownership of membership validation rules
- no ownership of message ordering guarantees
- no HTTP schema definitions
- no admin UI assumptions

## Preferred target tree

This is the rough end-state layout for the repo after the refactor stabilizes:

```text
main.py
readme.md
plan.md
requirements.txt

chat_core/
admin_app/
simulation/
tests/

db/
```

At the beginning of the refactor, `db/` can stay top-level to minimize disruption. Once the new structure settles, it can either remain a shared infrastructure package or move under `chat_core/infrastructure/`.
