# SkillLink threat model and security controls

This document states what SkillLink protects, who could attack it, which threats were
considered, and how each one is controlled in the code. It is written so the controls can be
checked against tests rather than taken on trust.

## 1. What is worth protecting

| Asset | Why it matters | Where it lives |
|---|---|---|
| Credentials and sessions | Account takeover leads to fraud and data loss | `users.password_hash`, the Flask session cookie |
| Personal data | Names, emails, phone numbers, universities, CV links | `users`, `freelancer_profiles`, `client_profiles` |
| Private conversations | Client and freelancer discuss work and prices | `messages` |
| Money records | Escrow deposits, releases and refunds are the audit trail | `transactions`, `email_outbox` |
| Uploaded files | Profile and portfolio images | `instance/uploads` |
| Admin capability | Can suspend users, moderate jobs, read all records | `/admin/*` routes |
| Availability | A marketplace that is down or flooded loses trust | the running process |

## 2. Actors and trust boundaries

```
anonymous ─┐
freelancer ─┼─► HTTPS reverse proxy ─► Flask app ─► MySQL
client     ─┤        (TLS, headers)     │  │
admin      ─┘                           │  └─► mail provider (SMTP)
                                        └────► local upload folder
```

Trust boundaries: the proxy is trusted only for the number of hops in `TRUSTED_PROXY_COUNT`;
everything in a request body, query string, header or uploaded file is untrusted; the database
is trusted as data, not as instructions.

## 3. Threats considered (STRIDE)

| STRIDE | Threat | Control | Where |
|---|---|---|---|
| Spoofing | Credential stuffing or password guessing | scrypt hashes, 10-character minimum with a letter and number, per-account lockout, per-address rate limit | `models/User`, `routes/auth.py`, `services/firewall.py` |
| Spoofing | Host-header injection / DNS rebinding | `ALLOWED_HOSTS` allow-list returns 400 for unknown hosts | `services/firewall.py` |
| Spoofing | Email spoofing of SkillLink mail | Configure SPF, DKIM and DMARC at the sending domain; the app never trusts inbound mail | deployment task |
| Tampering | Client edits the amount or project id in a form | The escrow amount is copied from `projects.agreed_amount`; ownership is re-checked server-side | `services/escrow.py` |
| Tampering | Direct SQL injection | SQLAlchemy parameter binding; no string-built SQL; injection signatures refused at the edge | `models/`, `routes/`, `services/firewall.py` |
| Tampering | Stored or reflected XSS | Jinja autoescaping, no `\|safe` on user data, CSP `script-src 'self'` | `templates/`, `app/__init__.py` |
| Tampering | CSRF on state-changing forms | Flask-WTF CSRF token on every form, verified on POST/PUT/PATCH/DELETE | `extensions.py`, templates |
| Repudiation | "I never approved that work" | Completion, release and refund are recorded with who did it, when, and provider references | `projects`, `transactions` |
| Information disclosure | Reading someone else's project, messages or ledger | Per-request membership checks; outsiders get 403; admin ledger is admin-only | `routes/marketplace.py`, `routes/dashboard.py` |
| Information disclosure | Cost data exposed to the wrong role | Only admin/pharmacist-style roles see sensitive fields; profile data is explicit | routes and templates |
| Information disclosure | Secrets in the repository | `.env` is gitignored; `.env.example` holds placeholders only | `.gitignore`, CI |
| Denial of service | Flooding the app or the login form | Fixed-window limits: global, write and auth scopes; oversized bodies refused before parsing | `services/firewall.py` |
| Denial of service | Expensive uploads | 5 MB cap, extension allow-list, magic-byte check | `config.py`, `forms.py` |
| Elevation of privilege | A client or freelancer reaching admin routes | `roles_required` on every admin route, plus ownership checks | `routes/dashboard.py` |
| Elevation of privilege | Disabled account keeps a live session | `load_user` returns `None`; suspension deletes sessions | `models/marketplace.py`, admin routes |
| Elevation of privilege | Session fixation | The session is cleared before `login_user()` | `routes/auth.py` |
| Tampering | Automated scanners probing for `.env`, `.git`, PHP shells, backups | Path allow-list denies them with 404 and records the attempt | `services/firewall.py` |
| Repudiation | An attacker denies scanning | Rejections are logged with address, path, rule and status; admins can review them | `/admin/security` |

## 4. OWASP Top 10 (2021) mapping

| Risk | Control in SkillLink |
|---|---|
| A01 Broken Access Control | Role decorators + ownership + participant checks on every route; admin ledger and security console are admin-only; tests assert 403 for outsiders |
| A02 Cryptographic Failures | Werkzeug scrypt password hashes (salted, per password); no plain-text secrets; `SESSION_COOKIE_SECURE` and HSTS in production; secrets from environment only |
| A03 Injection | SQLAlchemy parameter binding everywhere; Jinja autoescaping; firewall refuses classic injection payloads in query strings; LIKE searches use bound parameters |
| A04 Insecure Design | Escrow is a state machine (`held → released/refunded`) enforced in a service and by database CHECK constraints; work cannot be approved before funding; review requires a completed project |
| A05 Security Misconfiguration | Production refuses weak secrets, SQLite, insecure cookies and an unconfigured SMTP; CSP, `X-Frame-Options: DENY`, COOP/CORP, `X-Permitted-Cross-Domain-Policies`, referrer policy; debug off |
| A06 Vulnerable and Outdated Components | Requirements are bounded ranges; CI installs and runs the suite on every push; `pip-audit` is recommended in the deployment guide |
| A07 Identification and Authentication Failures | Password policy, per-account lockout (5 failures/5 min), per-address auth rate limit, generic error messages, timing equalisation for unknown emails, session rotation on login, admin accounts only via CLI |
| A08 Software and Data Integrity Failures | Alembic migration history is reviewed and reversible; deployment requires a backup before migrating; no unsigned third-party scripts beyond pinned CDN versions |
| A09 Security Logging and Monitoring Failures | Firewall rejections logged and visible to admins; application logs to stdout; `/health` for monitoring; database and mail failures recorded on rows |
| A10 Server-Side Request Forgery | The app never fetches a URL supplied by a user; portfolio and website links are rendered for the browser, not requested by the server |

## 5. The application firewall

Implemented in `app/services/firewall.py` and registered as a single `before_request` hook.
It runs before the view and before any database work.

| Rule | Trigger | Response | Rationale |
|---|---|---|---|
| `invalid_host` | Host not in `ALLOWED_HOSTS` (when configured) | 400 | Blocks host-header injection and DNS rebinding |
| `method_not_allowed` | Method outside GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS | 405 | Blocks TRACE and CONNECT probing |
| `suspicious_path` | `/.env`, `/.git`, `/.svn`, `wp-admin`, `phpmyadmin`, `*.php/.asp/.jsp/.cgi`, backup/DB/log files, traversal, null byte, backslash | 404 | Scanners learn nothing and produce no app errors |
| `suspicious_query` | `union select`, tautologies, `<script`, `drop table`, `sleep()/benchmark()`, `information_schema`, `../` | 400 | Cheap defence-in-depth in front of the ORM |
| `oversized_body` | `Content-Length` above `MAX_CONTENT_LENGTH` | 413 | Reject before reading the body |
| `unsupported_content_type` | Body present and not form-encoded or multipart | 415 | The app has no JSON write endpoints; refuses CSRF-bypass tricks |
| `rate_limit:global` | More than `RATE_LIMIT_GLOBAL` requests per window per address | 429 + `Retry-After` | Blunt flood protection |
| `rate_limit:write` | Same, for POST/PUT/PATCH/DELETE | 429 | Slows automated form abuse |
| `rate_limit:auth` | Same, for `/auth/*` | 429 | Slows credential stuffing alongside the per-account lockout |

Defaults: 300 requests per 60 s globally, 60 writes, 20 auth requests. `UP`/`static`, `/uploads`
and `/health` are exempt from counting so assets and probes do not consume a user's budget.

Every rejection is logged (`firewall blocked METHOD path from ip rule=...`) and stored in a
bounded in-memory ring buffer shown at `/admin/security`, together with per-rule counts and the
active configuration.

### Known limits of the firewall

- Counters and the review list are **per worker process and in memory**. With three gunicorn
  workers an address can use three times the limit, and a restart clears the list. Moving the
  counters to Redis is the correct next step for multi-worker deployments.
- It is not a substitute for a network firewall or a managed WAF. It reduces application-level
  noise; it does not stop a volumetric attack.
- Limits are keyed on `request.remote_addr`, so `TRUSTED_PROXY_COUNT` must be correct or every
  user shares one bucket.
- The signature list is intentionally narrow to avoid blocking legitimate searches; it catches
  obvious automated payloads, not novel ones.

## 6. Residual risks

| Risk | Why it remains | Mitigation to plan |
|---|---|---|
| No multi-factor authentication | Not implemented | Add TOTP for admin accounts first |
| No email verification | Accounts are usable immediately | Add a verification token and SMTP flow |
| Uploads on local disk | Simplest for a student deployment | Object storage with signed URLs |
| Simulated payments | No real money moves yet | Provider interface is ready; add callbacks and reconciliation before real money |
| In-memory rate limits | No Redis in the stack | Redis or a `login_attempts` table |
| No automated dependency scanning | CI runs tests only | Add `pip-audit` and Dependabot |
| Admin account is a single point of failure | CLI creation, no recovery flow | Documented recovery procedure, audit log |
| Verbose framework errors in debug mode | `DEBUG=True` locally | Production config sets `DEBUG=False`; never deploy debug |

## 7. How these controls are verified

```powershell
python -m unittest discover -v
```

| Area | Test module |
|---|---|
| Authentication, lockout, timing, session replacement | `tests/test_login_security.py` |
| Role and ownership enforcement | `tests/test_marketplace_workflow.py`, `tests/test_escrow_payments.py` |
| Escrow state machine and tampering | `tests/test_escrow_payments.py` |
| Message privacy and notification abuse | `tests/test_messaging_notifications.py` |
| Email abuse, retries | `tests/test_email_notifications.py` |
| Production guards, proxy handling | `tests/test_production_config.py` |
| Firewall rules, rate limits, headers, admin console | `tests/test_firewall.py` |
| Migrations on MySQL | CI job `mysql-migrations` |

## 8. If something goes wrong

1. **Suspected account compromise** - disable the account in the admin console (this deletes its
   sessions), then change the password and review `/admin/security`, `/admin/payments` and the
   account's projects.
2. **Leaked `SECRET_KEY`** - rotate it immediately; every session becomes invalid, then rotate
   the database and mail credentials and redeploy.
3. **Flood or scan in progress** - raise `RATE_LIMIT_*` only if legitimate users are affected;
   otherwise block the address at the proxy or hosting firewall and keep the app limits.
4. **Bad migration** - restore the backup taken before the deploy, fix the migration, and
   redeploy; MySQL DDL is not transactional, so partial state is expected after a failure.
5. **Lost uploads** - restore the volume or object storage snapshot; do not re-run the app to
   recreate them.
