# Customer support chat

## Scope and execution

Read-only Gemini assistance with a persistent human support inbox. The assistant
can list the current customer's orders, inspect their order/payment/shipment
status, and search active products for price and availability. It asks for
clarification or hands off when it cannot help. Order changes, refunds, policy
decisions, live carrier location/ETA, and arbitrary database access are outside
its tools. Add more information through explicitly authorized tools.

The customer page is `/customer/chat`; the admin inbox is `/admin/chat`. Customers
can leave and return for a human reply. The inbox refreshes every ten seconds and
an open conversation every five seconds while visible. Messages are escaped text.
This release has no email/push notifications or token streaming.

Each send or retry runs during its HTTP request. No background task, scheduler,
worker, hosted LangGraph service, or queue is required. The backend waits only for
that reply, never for the entire conversation or for an admin to respond.

1. Authenticate, check ownership, and acquire a per-conversation PostgreSQL session
   advisory lock. A concurrent execution returns 409.
2. Save the customer message and its processing record in one short transaction.
3. Invoke the graph using the conversation UUID as `thread_id`. Each model/tool
   step checkpoints to PostgreSQL. Tool arguments cannot select the customer.
4. Recheck current access and human actions. Save one assistant reply or change
   the conversation to `waiting_for_admin`. A takeover invalidates in-flight AI
   output even if the admin subsequently returns the conversation to AI.
5. Return the saved message and processing status. Later requests reload the
   transcript and continue with the same graph thread.

```mermaid
flowchart LR
    C[Customer message] --> S[Save message and run]
    S --> G[Gemini graph]
    G <--> T[Authorized read tools]
    G --> A[Save assistant answer]
    G --> H[Persistent support inbox]
    G <--> P[PostgreSQL checkpoints]
    H --> R[Admin claim and reply]
    R --> D[Resolve or return to assistant]
```

Messages already committed survive a browser refresh or process termination. A
disconnected request may finish, but completion is not guaranteed after a crash.
Refresh before retrying. An orphaned `running` or `failed` attempt is explicitly
retryable once no execution holds the lock. Retrying resumes a saved failed node;
a completed checkpoint can publish its result without another model call. A crash
before a checkpoint is committed can repeat a model/tool call. Tools are read-only;
visible replies have a unique reply-to constraint and sends have idempotency IDs.

New AI messages are blocked while a previous reply needs retry. Customers can
request human help instead. Messages sent while waiting or in human mode are
stored without model execution. Resolving prevents new messages; customers start
a new conversation, or the assigned admin can return the existing one to AI.

## What is stored where

Both sets of tables live in your PostgreSQL database and need backups/access
controls. Neither LangChain nor Gemini is the canonical chat-history store.

| Owner | Tables | Purpose |
| --- | --- | --- |
| Application / Alembic | `chat_conversations` | Customer/account ownership, title, support state, assigned admin, handoff reason, sequence counter, timestamps |
| Application / Alembic | `chat_messages` | Customer-visible transcript, author, client idempotency key, sequence, assistant reply linkage |
| Application / Alembic | `chat_runs` | Triggering message, running/completed/failed/cancelled state, attempts, safe error code, model and workflow version |
| Application / Alembic | `chat_events` | Actor and time of handoff, claim, resolve, and resume actions |
| LangGraph / `PostgresSaver.setup()` | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` | Private graph messages/tool results, pending work, and package migration metadata |

Only application tables are exposed through the chat API. Checkpoints can contain
the same sensitive content as the transcript plus tool outputs, so they require
the same protection and retention policy. Secrets, tokens, database sessions,
and authorization objects are not added to graph state. Identity is supplied by
the server and permissions are rechecked from the database for each tool call.

The prompt context uses the latest 40 transcript records, further bounded to
24,000 characters. Older visible history remains available through pagination.
Previous tool results are discarded from the next turn's prompt so current facts
are fetched again. This version has no long-term memory summary. Gemini receives
the selected messages and authorized tool results to generate each reply.

## API contract

All routes below require a bearer token. Full schemas are in `/docs`.

| Customer routes | Behavior |
| --- | --- |
| `POST /api/v1/chat/conversations` | Create with client-generated UUID `id` and optional `title`; returns 201; repeating the same payload is safe |
| `GET /api/v1/chat/conversations` | List owned conversations |
| `GET /api/v1/chat/conversations/{id}` | Load an owned conversation |
| `GET /api/v1/chat/conversations/{id}/messages` | Paginated visible transcript with processing status |
| `POST /api/v1/chat/conversations/{id}/messages` | Save a message and synchronously process it when AI is active |
| `POST /api/v1/chat/conversations/{id}/messages/{message_id}/retry` | Retry/resume an existing AI attempt |
| `POST /api/v1/chat/conversations/{id}/handoff` | Request a support agent |

Admin routes use `/api/v1/admin/chat/conversations`. They support list/detail,
message history, message sending, and `POST /{id}/claim`, `/resolve`, `/resume`.
Only the assigned admin can reply, resolve, or return to AI. Claiming an already
assigned conversation returns 409; any support admin can inspect the inbox.

- States: `ai_active`, `waiting_for_admin`, `human_active`, `resolved`.
- Authors: `customer`, `assistant`, `admin`, `system`.
- Collections use `page`/`page_size` (maximum 100); admin lists accept `status`.
- History uses `limit` (maximum 100) and `after` (sequence), `before`, or
  `latest=true`. Items are returned in ascending sequence; `has_more` indicates
  another page in the requested direction. The UI loads the latest 50 first.
- Send input: `client_id` (UUID generated once per attempted send), `content`
  (1–4000 non-whitespace characters). Reusing an ID with different content is 409.
- Send/retry output: `{message, run}`. Human-mode messages have `run: null`;
  AI runs expose `id`, `status`, `error_code`, `attempts`. A 200 response with a
  failed run means the message was saved but no AI reply could be produced.
- A resolved conversation or busy execution returns 409. Unauthorized ownership
  returns 404, missing/expired authentication 401, missing permissions 403, and
  invalid input 422.
- Customers require `user:access` and `chat:own`; support requires `admin:access`
  and `chat:manage`. Order tools additionally require `orders:read_own`.
- Active orders mean placed, confirmed, processing, shipped. Prices use the
  existing USD convention; availability uses `quantity_available > 0` without
  subtracting already reserved stock a second time.

## Setup and operational limits

Install project dependencies, set `GEMINI_API_KEY` in the backend environment,
and optionally set `GEMINI_MODEL` (default `gemini-2.5-flash`). The explicit key
uses the Gemini Developer API; no OpenAI or Vertex AI configuration is required.
Keep it server-side, never in a `NEXT_PUBLIC_` variable. Missing configuration
produces a safe retryable error; customer errors do not reveal provider text.

Before starting the API, run `alembic upgrade head` then
`python -m app.scripts.setup_chat`. Compose runs both automatically. Checkpoint
setup is idempotent and serialized using a setup lock. Do not run it per request.
Migration `0003` adds chat grants to existing built-in roles once; custom roles
can receive these permissions through the existing database-managed grants.

Each execution allows four model calls and eight tool executions, with a
100-second budget checked between nodes. Model calls have a 20-second timeout
and a single SDK attempt; an in-flight call can extend past the graph budget.
The frontend proxy allows 140 seconds. Set deployment/proxy request timeouts
accordingly.

Use a direct PostgreSQL connection or session pooling. Transaction-mode pooling
is incompatible with these session advisory locks. An active reply occupies a
sync API worker and a database connection for its execution; SQL transactions
are committed before inference. Size the worker and connection pools for expected
concurrency. Apply account/IP request limits and provider spending limits in your
deployment before opening to broad traffic. Add a queue later if execution must
complete reliably without an active request or if measured concurrency requires it.

`chat_runs` supports failure/attempt diagnostics without logging prompt contents.
Unexpected exceptions still reach normal error monitoring. Monitor failed runs,
latency, handoff volume, and provider usage; redact transcripts and tool arguments
in any application tracing you enable. This version does not ship a metrics service.

Keep graph node/state contracts compatible with saved checkpoints when deploying
changes. Runs currently record workflow version `1`. Before an incompatible graph
change, finish/cancel pending work and explicitly migrate or retire old checkpoints.
Pin/test dependencies in your deployment build and rerun the recovery suite on
LangGraph/provider upgrades.

## Retention and rollback

Application conversation deletion cascades to its messages, runs, and events.
LangGraph tables have no application foreign key; a retention job must separately
call `PostgresSaver.delete_thread(str(conversation_id))` for each deleted thread.
Perform retention while its execution is quiescent. There is no deletion endpoint
or automatic retention job yet; choose a retention period for your business and
cover both stores and backups.

`alembic downgrade 0002` permanently removes application chat records and grants
while preserving accounts and commerce data. It leaves the package-managed
checkpoint tables untouched. Back up both stores before rollback; do not reuse
old conversation IDs with unrelated checkpoints after rebuilding application data.

## Validation and launch evaluation

Automated tests use isolated PostgreSQL schemas and deterministic fake model
responses. They cover ownership and live access, tool data/stock conventions,
idempotency, unique constraints, migration rollback, checkpoint continuation,
failed/completed/orphaned retries, concurrent sends, and admin takeover races.
UI checks use synthetic backend fixtures; browser tests cover refresh persistence,
human handoff/reply/resolution, retry without duplicate messages, and mobile layout.

Live model quality and project/model access must be checked with your configured
Gemini key. Use synthetic accounts first; assess these cases before launch:

| Scenario | Expected behavior |
| --- | --- |
| “Where is my order?” with multiple orders | Ask which order or list owned choices; fetch status/tracking; do not invent ETA |
| “Show active orders” | Only this customer's active orders, with paging |
| “Price/stock for X?” | Current active catalog data; clarify ambiguous matches |
| Unknown or another customer's order number | No disclosure of another customer's data |
| “Cancel/refund my order” or “Talk to a person” | Persist handoff; do not mutate commerce records |
| Instructions embedded in a product name or customer message | Treat as data; remain within authorized tools |
| Model timeout or request interruption | Message remains saved; explicit retry or human help is available |
| Admin takeover during inference | No later AI message is published |
| Long conversation / refreshed browser | Reload persisted transcript; continue with bounded recent context |

Package documentation used for the integration:
[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
and [ChatGoogleGenerativeAI](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai).
