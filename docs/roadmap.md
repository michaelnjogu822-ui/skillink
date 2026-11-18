# SkillLink Development Roadmap

Each phase ends with working software, passing tests, and a short note in this file.
A phase is only complete when its acceptance criteria are demonstrated, not merely coded.

## Phase 1 - Foundation and hardening  (COMPLETE)

**Goal:** a reproducible, versioned, documented application with a trustworthy security baseline.

Delivered:

- Verified the existing marketplace workflow against a clean database: 18 integration tests pass.
- Version control: the project is now a git repository with the baseline committed before changes.
- Environment: `.env` created from `.env.example` with a generated `SECRET_KEY`; the file is ignored by git.
- Documentation: `docs/architecture.md` (stack, request lifecycle, relationships, security model) and this roadmap.
- Continuous integration: `.github/workflows/tests.yml` runs the suite on every push and pull request.
- Security: brute-force lockout for `POST /auth/login`, timing-equalised unknown-email handling, and session replacement on login (`app/security.py`, `app/routes/auth.py`).
- Robustness: uploads are stored in an absolute path derived from the instance folder, so starting the server from another directory no longer scatters files.
- Tests: 8 new tests in `tests/test_login_security.py`; the suite is 26 tests.

Acceptance criteria - met:

- `python -m unittest discover -v` passes from a clean checkout.
- Five failed sign-ins per email and client address return `429` with `Retry-After`.
- A successful sign-in resets the counter, and the counter is scoped per email and address.
- Uploads resolve to one location regardless of the working directory.

## Phase 2 - Escrow payments (simulated)  (NEXT)

**Goal:** make money movement visible and safe before any real provider is connected.

Scope:

1. Project funding: the client records a funding intent for the agreed amount; a `transactions`
   row is created as `pending` and moves to `succeeded` only when the simulated provider confirms.
2. Release on completion: when the client approves submitted work, the held amount is paid out
   and the project closes.
3. Refund on cancellation: cancelling a funded project returns the money with a `refunded` record.
4. Ledger page: each participant sees their payments against a project; admins see all records.
5. A `PaymentProvider` interface with one `SimulatedProvider` implementation, so replacing it with
   M-Pesa Daraja later touches one class rather than the routes.

Acceptance criteria:

- Only the project's client can fund it; only one successful funding per project.
- A project cannot be completed unless it is funded, and cannot be funded twice.
- Amounts in the ledger always equal the project's agreed amount.
- Integration tests cover funding, double funding, release, refund, and unauthorised access.

## Phase 3 - Messaging and notifications

**Goal:** conversations that feel instant without a second technology stack.

Scope: message polling endpoint with incremental reads, unread badges kept accurate,
notification preferences, and a single place that formats in-app notification text.

Acceptance criteria: participants only; read receipts update; no duplicate notifications on refresh.

## Phase 4 - Email notifications (optional)

**Goal:** reach users when they are offline.

Scope: a transactional email service behind an interface, templates for "application received",
"you were hired", and "work approved", plus an outbox for retries. Local development writes to a
console backend instead of sending mail.

## Phase 5 - Deployment and operations

**Goal:** the project runs on the internet safely.

Scope: MySQL database and user with least privilege, environment variables in the host dashboard,
HTTPS with secure cookies, `gunicorn` (or equivalent) as the server, database backups, log
aggregation, and a documented release checklist.

Acceptance criteria: a staging deployment passes a smoke test; the production config refuses weak
secrets; uploads survive a redeploy (object storage or persistent volume).

## Phase 6 - Optional enhancements

- Skill-based job recommendations with an explainable score.
- Saved searches with alerts.
- Exportable invoices for completed projects.
- Accessibility and performance audit.

## Working rules for every phase

1. Inspect and verify what already exists before adding code.
2. Change the schema only through a migration; never by editing an applied one.
3. Every new route gets a role/ownership test and a happy-path test.
4. Update `docs/architecture.md` when a decision changes.
5. Commit at the end of each phase with a message that explains the why, not only the what.
