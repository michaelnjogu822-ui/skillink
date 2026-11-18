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

## Phase 2 - Escrow payments (simulated)  (COMPLETE)

**Goal:** make money movement visible and safe before any real provider is connected.

Delivered:

- `app/services/escrow.py` with a `PaymentProvider` interface, a `SimulatedProvider` that
  settles instantly, a provider registry, and `fund_project` / `release_escrow` /
  `refund_escrow` transitions.
- `transactions.kind` (`manual` or `escrow`) and `transactions.settlement_reference`, plus a
  widened status CHECK allowing `held` and `released` (migration `c3f8a1d64e21`).
- Client funding on the project page, gated approval that releases payment to the freelancer,
  and cancellation that refunds held money to the client.
- Freelancer earnings count released money only, so funds still held are never reported as
  earned; the client's spending page shows held, released and refunded records.
- An administrator ledger at `/admin/payments` listing every deposit, release and refund.
- 20 new tests in `tests/test_escrow_payments.py`; the suite is 46 tests.

Acceptance criteria - met:

- Only the project's client can fund it, and a second funding attempt is refused.
- Work cannot be approved before the escrow is funded; approval releases the exact agreed amount.
- Cancelling a funded project refunds it; cancelling an unfunded project writes no transaction.
- Ledger amounts always equal the project's agreed amount.
- Illegal statuses are rejected by the database, not only by Python.

## Phase 3 - Messaging and notifications  (COMPLETE)

**Goal:** conversations that feel instant without a second technology stack.

Delivered:

- An incremental message feed, `GET /projects/<id>/messages.json?since_id=`, that returns only
  new messages plus the viewer's unread count, and `POST /projects/<id>/messages/read` to mark
  the conversation read. Both check project membership on every request.
- `static/js/messages.js` polls every five seconds while the project page is visible, appends
  new messages, marks them read and keeps the navigation badge accurate.
- One source for unread counts (`app/services/messages.py`) shared by the badge, the inbox and
  the polling endpoint.
- One writer for notifications (`app/services/notifications.py`): every call site now passes a
  kind and facts instead of hand-built text, so wording lives in a single table and a muted
  category can skip the row.
- Per-user notification preferences at `/settings/notifications` across applications, projects,
  messages, payments and reviews, stored in the new `notification_preferences` table
  (migration `d94b2f7c1a08`).
- 16 new tests in `tests/test_messaging_notifications.py`; the suite is 62 tests.

Acceptance criteria - met:

- Only the project's participants (and administrators, read-only) can read the feed; outsiders
  receive 403 and anonymous visitors are redirected to sign in.
- The feed returns only messages after `since_id`, and the read marker clears the unread count.
- Muting a category suppresses its notification without suppressing the underlying message,
  application or payment.
- Notification text is produced by one formatter and covered by tests.

## Phase 4 - Email notifications  (COMPLETE)

**Goal:** reach users when they are offline.

Delivered:

- `app/services/email.py`: event registry, three backends (console, memory, SMTP) behind an
  `EmailBackend` interface, and `queue` / `flush` helpers.
- An `email_outbox` table (migration `e5a7c9b1f302`) so an email is a durable row, not a call that
  can be lost. Delivery failures stay on the row with attempts and the last error.
- Four email events: an application received (to the client), being hired, work approved, and
  cancellation with or without a refund (to the freelancer), each rendered from a text template.
- Email switches per notification category (`notification_preferences.email_enabled`), independent
  of the in-app switches, editable on the same settings page.
- A `flask send-emails` command with `--retry-failed` for retries, and automatic delivery after a
  successful state-changing request.
- 12 new tests in `tests/test_email_notifications.py`; the suite is 74 tests.

Acceptance criteria - met:

- Applying, hiring, approving and cancelling queue the right email to the right person.
- A delivery failure is recorded and retried later without failing the business action.
- Turning email off for a category stops that email only; the in-app notification still appears.
- A message is not retried beyond `MAX_ATTEMPTS`.

## Phase 5 - Deployment and operations  (NEXT)

**Goal:** the project runs on the internet safely.

Scope: MySQL database and user with least privilege, environment variables in the host dashboard,
HTTPS with secure cookies, `gunicorn` (or equivalent) as the server, database backups, log
aggregation, and a documented release checklist.

Acceptance criteria: a staging deployment passes a smoke test; the production config refuses weak
secrets; uploads survive a redeploy (object storage or persistent volume).

## Phase 6 - Security hardening and threat model  (APPROVED)

**Goal:** defend the application against the threats a public marketplace attracts.

Scope: an application-layer firewall (per-address rate limits, scanner path blocking, trusted
proxy handling, request size and content-type checks), tightened security headers, and a written
threat model mapping the OWASP Top 10 to the controls implemented here, each with a test.

## Phase 7 - Optional enhancements

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
