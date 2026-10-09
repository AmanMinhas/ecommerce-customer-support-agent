# Agent instructions

These instructions apply throughout this repository. Follow them for every task.

## Mandatory test-driven development (TDD)

- Always use TDD for executable changes: new features, bug fixes, behavior changes,
  and refactoring. Do not write production code first and add tests afterward.
- **Red:** Write the smallest meaningful test describing the requested behavior.
  Run it before changing production code and confirm that it fails for the expected
  missing behavior or bug, rather than an import, environment, or setup error.
- **Green:** Make the smallest production change needed to pass that test. Run the
  focused tests again and confirm they pass.
- **Refactor:** Improve structure only with passing tests, preserving behavior.
  Rerun the affected tests after each refactor.
- Repeat this cycle in small increments. For a bug, first add a regression test
  that reproduces it. For a behavior-preserving refactor, first run existing tests
  and add any missing characterization tests; those tests should pass before and
  after the refactor. Do not deliberately break working behavior to manufacture red.
- Test observable behavior and contracts, not private implementation details.
  Cover relevant success, invalid-input, boundary, and failure cases. Keep tests
  deterministic and independent; avoid real external services and timing sleeps.
- Mock external boundaries when appropriate, but use integration tests for behavior
  that depends on database constraints, transactions, or PostgreSQL semantics.
- Never delete, skip, weaken, or rewrite a valid test simply to make a change pass.
  Update expectations only when the requested behavior intentionally changes.
- If a required test cannot run, resolve the setup issue first. If blocked, report
  the exact blocker and leave the work explicitly unverified; do not claim a
  completed TDD cycle or silently proceed with untested production changes.
- Documentation-only changes do not require artificial tests. Validate their
  content and any commands or links instead. Configuration or migration changes
  that affect runtime behavior require appropriate tests or executable checks first.

## Repository and development workflow

- This is a Python 3.12+ FastAPI backend using Pydantic, SQLAlchemy 2, PostgreSQL,
  and Alembic. Inspect relevant code, configuration, and README instructions before
  editing; follow existing patterns and keep changes focused on the requested work.
- Inspect `git status` before editing. Preserve unrelated and uncommitted user work.
  Avoid destructive Git commands, database resets, and volume deletion unless
  explicitly authorized.
- Put tests under `tests/`, using pytest. The project declares pytest, HTTPX, and
  Ruff in the `dev` optional dependencies. If tests or fixtures are missing, create
  the minimum necessary test setup before implementing behavior.
- Use the project's environment. Install development dependencies when needed with
  `python -m pip install -e '.[dev]'`, subject to environment permissions.
- Run focused tests during each TDD cycle, then run the full suite and lint checks
  before handing off executable changes:

  ```bash
  python -m pytest tests/path_to_test.py -q  # Replace with the actual test path.
  python -m pytest -q
  python -m ruff check .
  git diff --check
  ```

- Report the commands actually run and their outcomes. Distinguish new failures
  from pre-existing failures; never present a skipped check as passing.

## Implementation best practices

- Prefer small, readable functions and explicit names. Add type annotations to
  new or changed public interfaces. Avoid speculative abstractions, unnecessary
  dependencies, and unrelated cleanup.
- Validate API input with Pydantic and preserve documented response schemas,
  status codes, pagination, and error behavior unless changes are requested.
- Keep business logic testable and avoid duplicating rules across API handlers.
  Handle expected errors explicitly without hiding unexpected exceptions.
- Use SQLAlchemy expressions or bound parameters for queries. Manage session and
  transaction lifecycles explicitly; roll back failed operations and avoid partial
  writes. Check query counts and pagination where collections are involved.
- Use Alembic migrations for schema changes. Do not rewrite applied migrations.
  Test migrations against an isolated database and document rollback limitations.
- Never run tests against production or a user's working database. Use isolated
  test data and cleanup fixtures; use PostgreSQL when database semantics matter.
- Use decimal arithmetic for money and preserve currency and rounding conventions.
  Maintain order, payment, and inventory invariants with transactional operations.
- Never commit or log credentials, `.env` contents, payment details, or customer
  personal data. Use synthetic fixtures and existing environment-based settings.
- Update documentation when setup, API contracts, or operational behavior changes.

## Communication and context restoration

- Assume the user is multitasking and may return without prior context.
- When resuming work, answering a status question, or handing off completed work,
  provide a concise, self-contained recap covering the requested outcome, changes
  made, how the implementation satisfies the request, and current status.
- Include completed checks and anything pending or blocked. Preserve important
  constraints and decisions while omitting incidental tool logs.
- For executable changes, include the observed red and green results and any
  remaining validation limitations in the handoff.
