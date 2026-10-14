# Support chat implementation validation

Implemented the read-only customer assistant and persistent admin inbox, using
Gemini with `GEMINI_API_KEY`, LangGraph PostgreSQL checkpoints, and synchronous
per-message execution without a queue. See [design and setup](chat.md).

## Automated results

Backend checks were rerun on October 15, 2026 after rebasing onto remote `main`.
Pytest used a fresh disposable PostgreSQL cluster with isolated schemas and fake
model responses. Frontend results below are from the earlier implementation
validation; the companion UI is outside this backend PR and was not rerun here.

| Command | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | 60 passed; isolated PostgreSQL schemas, fake model responses |
| `.venv/bin/python -m ruff check app tests alembic/versions/0003_support_chat.py` | Passed |
| `.venv/bin/python -m ruff check .` | Two pre-existing I001 import-order findings in `alembic/env.py` and `alembic/versions/0001_initial_schema.py` |
| `git diff --check` | Passed |
| `npm test` (UI) | 85 passed across 10 files |
| `npm run lint` (UI) | Passed |
| `npm run typecheck` (UI) | Passed |
| `npm run format:check` (UI) | Passed |
| `npm run build -- --webpack` (UI) | Passed, including all chat pages and API routes |
| `PLAYWRIGHT_CHANNEL=chrome npm run test:e2e` (UI) | 9 passed using synthetic fixture servers |

The default Turbopack build encountered an environment restriction when binding
its worker port. The supported webpack build completed successfully and was used
for the browser checks. The two Ruff findings were reproduced by linting the
unchanged files from `origin/main`; applied migrations were not rewritten.
Pytest also emits the existing Alembic `path_separator` deprecation warning.

Desktop customer/admin and mobile customer screenshots were inspected. They are
available in the companion UI's ignored `test-results/` folder. Tests did not
contact the working backend, working database, or Gemini. The installed Gemini
SDK also accepted the tool schemas in a separate offline construction check.

## TDD evidence

Tests were written and run before the corresponding behavior was added. Observed
red-to-green cycles covered schema/ownership, message persistence, handoff,
checkpoint continuation, retry recovery, pagination, UI/API bridging, and navigation.
Later regression cycles included:

- HTTP transport timeout/connection failures: two new cases failed with uncaught
  provider errors; all three failure/retry cases passed after handling transport
  errors as saved retryable attempts.
- Cancellation with out-of-order transaction timestamps: the regression failed
  because a cancelled attempt became completed; all 17 execution tests passed
  after honoring durable cancellation before publishing a reply.
- Process death between message acceptance and execution: a missing durable run
  was reproduced, then fixed by saving the message and attempt atomically.
- Completed checkpoint recovery and takeover during inference: persisted results
  replay without another model call, and a human takeover suppresses AI output.
- Revoked UI access: previously displayed chat data is cleared when a refresh
  returns an authorization or ownership failure.

## Remaining environment checks

Live Gemini authentication, project quota/model access, and generated-answer
quality were not tested during PR preparation. These require the configured
backend environment and the synthetic launch cases in the chat guide. PR checks
did not apply migrations to the working application database. Compose runs
application and checkpoint setup on rebuild/start.

The backend PR branch is `codex/gemini-support-chat-pr`, rebased onto `origin/main`,
which already contains the authentication feature. The PR includes backend code,
migrations, tests, and documentation only. Local credentials, generated output,
and the companion frontend workspace are excluded. No deployment is part of this
change. The PR remains a draft while the required full-repository Ruff check fails.
