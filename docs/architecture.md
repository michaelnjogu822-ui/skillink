# SkillLink Architecture

SkillLink is a student freelancing marketplace for Kenyan universities and colleges.
Freelancers publish profiles and apply to jobs; clients post jobs, compare applicants,
hire, and review completed work; administrators moderate users, jobs, and reports.

This document explains how the current code is organised and why, so each layer can be
defended or changed deliberately rather than by guesswork.

## 1. Technology stack

| Layer | Choice | Reason |
|---|---|---|
| Web framework | Flask 3 (application factory + blueprints) | Small, explicit, easy to trace a request from URL to function |
| Templates | Jinja2 | Server-rendered HTML works without JavaScript, is indexable, and keeps forms simple |
| ORM | SQLAlchemy 2 via Flask-SQLAlchemy | Typed models, relationships, and one query API for SQLite and MySQL |
| Migrations | Alembic via Flask-Migrate | The schema has a versioned history instead of being recreated by hand |
| Database | SQLite for development, MySQL 8 for deployment | Zero setup locally; a real server database anywhere else |
| Forms and CSRF | Flask-WTF / WTForms | Validation and CSRF protection in one place |
| Sessions | Flask-Login (signed cookie) | Standard session handling, `current_user`, and role checks |
| Passwords | Werkzeug `scrypt` hashes | Slow, salted, and built into Werkzeug - no native build step |
| Styling | Bootstrap 5 + custom CSS | Responsive layout without writing a design system from scratch |

## 2. Runtime components

```
Browser
  │  HTTP (form posts and page loads, CSRF token in every form)
  ▼
Flask app (app/__init__.py: create_app)
  ├── before_request: Flask-Login loads the user from the session cookie
  ├── blueprints: auth, marketplace, dashboard, main
  ├── Flask-WTF validates form input and the CSRF token
  ├── SQLAlchemy session → connection pool → SQLite file or MySQL server
  └── Jinja template → HTML response + security headers
```

`create_app()` is the only place that assembles the application. Tests call it with
`config_overrides`, which is why the suite can point at an in-memory database and a
throwaway upload folder without touching real configuration.

## 3. Request lifecycle (example: hiring a freelancer)

1. The client submits `POST /jobs/<id>/hire/<application_id>` with a CSRF token.
2. `@login_required` and a role check confirm a signed-in client owns the job.
3. The route validates that the application belongs to that job and is still `pending`.
4. Inside one transaction it: accepts the application, rejects the other applications for
   the job, creates the `projects` row, and updates the job status to `in_progress`.
5. A notification row is written for the freelancer.
6. The transaction commits once; any failure rolls the whole hire back.
7. The response redirects (POST/redirect/GET) so a refresh cannot repeat the hire.

## 4. Directory map

```
app/
  __init__.py          application factory, CLI commands, error pages, security headers
  extensions.py        db, migrate, login_manager, csrf singletons
  config.py (root)     environment-driven configuration classes
  security.py          login throttling and timing equalisation
  forms.py             every form and its validation rules
  models/marketplace.py all tables and their constraints
  routes/
    main.py            landing page, health check
    auth.py            register, login, logout
    marketplace.py     jobs, applications, freelancer search, hiring
    dashboard.py       role dashboards, profile, projects, messages, reviews, admin
  templates/           base layout, page templates, reusable macros
  static/              CSS and small JavaScript enhancements
migrations/versions/   ordered schema history
tests/                 integration tests (unittest)
docs/                  this document and the roadmap
```

## 5. Data model and relationships

```
users ──1:1──> freelancer_profiles ──N:M──> skills          (through freelancer_skills)
  │                     └──1:N──> portfolios
  ├──1:1──> client_profiles
  ├──1:N──> jobs                (client_id)
  ├──1:N──> job_applications     (freelancer_id)
  ├──1:N──> projects            (as client and as freelancer)
  ├──1:N──> messages            (sender_id)
  ├──1:N──> notifications
  ├──1:N──> reviews             (as reviewer and as reviewee)
  ├──1:N──> transactions        (as payer and as payee)
  └──1:N──> reports             (as reporter and as reported user)

jobs ──1:N──> job_applications ──0:1──> projects
jobs ──1:1──> projects         (a job is hired once; job_id is unique)
projects ──1:N──> messages
projects ──1:N──> reviews
projects ──1:N──> transactions
```

Key integrity rules enforced by the database, not only by Python:

| Rule | Where |
|---|---|
| One application per freelancer per job | `uq_application_job_freelancer` |
| One project per job | unique `projects.job_id` |
| One review per person per project | `uq_review_project_reviewer` |
| Client and freelancer must differ (projects, transactions, reviews, reports) | `CHECK` constraints |
| Status values are closed sets | `CHECK (status IN (...))` on every status column |
| Ratings are 1-5, amounts are positive | `CHECK` constraints |
| Deleting a user removes their profile, applications and notifications | `ON DELETE CASCADE` |
| Users with recorded work cannot be silently deleted | `ON DELETE RESTRICT` on projects, reviews, transactions |

These constraints are why the app can trust the database even if a future route forgets a
check.

## 6. Payments and escrow

Money never moves inside a route. A route validates who is asking, locks the project row,
and calls `app/services/escrow.py`, which calls a `PaymentProvider`.

```
Client funds      charge()   client  -> escrow      transactions.status = held
Client approves   payout()   escrow  -> freelancer  transactions.status = released
Client cancels    refund()   escrow  -> client      transactions.status = refunded
```

Properties this design gives us:

| Property | How it is guaranteed |
|---|---|
| One escrow record per project | `escrow_transaction()` lookup inside the route's `SELECT ... FOR UPDATE` transaction |
| Amounts cannot drift | The transaction copies `project.agreed_amount`; there is no amount input in the request |
| Only the client can fund/cancel | `@role_required("client")` plus an explicit `project.client_id == current_user.id` check |
| Work cannot be approved unfunded | `complete_project` refuses unless the escrow state is `held`, then releases in the same transaction |
| Cancelling refunds held money | `cancel_project` refunds only when the state is `held`, so an unfunded cancellation writes nothing |
| Illegal statuses are impossible | `ck_transactions_status` allows only `pending, held, released, succeeded, failed, refunded`; `ck_transactions_kind` allows only `manual, escrow` |
| Provider can be replaced | Routes depend on the `PaymentProvider` interface; `PROVIDERS` maps a name to a class and `PAYMENT_PROVIDER` selects it |
| A repeated request cannot double-charge | The second `fund` finds an existing `held`/`released` record and returns a warning |

Escrow rows are `kind = 'escrow'`; rows imported or recorded outside the flow are
`kind = 'manual'`. Freelancer earnings count `released` and legacy `succeeded` money, so
funds sitting in escrow are visible but are never counted as earned.

## 7. Security model

| Threat | Control |
|---|---|
| Password theft | `scrypt` hashes with per-password salt; passwords never logged or stored in plain text |
| Brute-force login | `app/security.py` locks a (client address, email) pair after `LOGIN_MAX_FAILURES` failures in `LOGIN_WINDOW_SECONDS` and answers `429` with `Retry-After` |
| Account enumeration | Unknown emails run a dummy hash comparison, so response timing matches a real account; the error message is identical |
| Session fixation | The session is cleared before `login_user()`, so a pre-login cookie cannot carry over |
| Cross-site request forgery | Flask-WTF tokens on every state-changing form |
| SQL injection | All queries through SQLAlchemy parameter binding; no string-built SQL |
| Cross-site scripting | Jinja autoescaping; user data is never rendered with `\|safe` |
| Malicious uploads | Extension allow-list, magic-byte signature check, server-generated filenames, 5 MB cap, files served from a dedicated folder |
| Clickjacking, MIME sniffing, mixed content | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, and a CSP in `after_request` |
| Privilege escalation | Role checks on every admin route plus ownership checks (a client can only edit their own job) |
| Suspended accounts | `load_user` returns `None` for inactive users, and admin suspension clears their sessions |
| Production misconfiguration | The app refuses to boot in production without a strong `SECRET_KEY`, a server database, and secure cookies |
| Payment tampering | Amounts come from the project row, transitions are checked by the service and by database CHECK constraints, and every state change runs under a row lock |

Limitations to state honestly in a defence:

- The login throttle is in process memory, so several worker processes each keep their own
  counters. A production deployment should move this to Redis or a database table.
- `request.remote_addr` is the direct client. Behind a proxy, configure trusted proxies so
  the real address is used.
- File uploads are stored on local disk. Production should use object storage.
- Escrow is simulated. A live provider must add authorisation, callbacks, reconciliation and
  refund handling; the interface is ready for it but the provider is not.

## 8. Configuration

Configuration comes from environment variables, loaded by `run.py` through `python-dotenv`.

| Variable | Meaning |
|---|---|
| `SECRET_KEY` | Signs session cookies. At least 32 characters in production. |
| `DATABASE_URL` | SQLAlchemy URL. Empty means SQLite at `instance/skilllink.db`. |
| `UPLOAD_FOLDER` | Upload directory. Empty means `instance/uploads` next to the database. |
| `SESSION_COOKIE_SECURE` | `true` when served over HTTPS (required in production). |
| `APP_ENV` | `production` enables `ProductionConfig` and its startup checks. |
| `LOGIN_MAX_FAILURES` | Failed sign-ins allowed per email and client address (default 5). |
| `LOGIN_WINDOW_SECONDS` | Length of that window in seconds (default 300). |
| `PAYMENT_PROVIDER` | Provider used for escrow (default `simulated`). |

## 9. Running and testing

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # then set SECRET_KEY
$env:FLASK_APP = "run.py"
flask db upgrade                 # create the schema
flask create-admin admin@example.co.ke "SkillLink Administrator"
flask run
```

```powershell
python -m unittest discover -v        # full suite
python -m unittest tests.test_login_security -v   # one module
python -m unittest tests.test_escrow_payments -v   # escrow lifecycle
```

The suite builds its own application with an in-memory SQLite database, so it never touches
your development data. Integration tests drive real HTTP requests through Flask's test
client, including CSRF tokens and role checks.
