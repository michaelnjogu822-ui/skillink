# Deploying SkillLink

This guide takes the application from a local checkout to a working HTTPS deployment
backed by MySQL 8. Every step was exercised on a real MySQL 8.0.46 instance while
building the project, including a full migrate → use → downgrade → re-migrate cycle.

## 1. What you need

| Requirement | Notes |
|---|---|
| Python 3.12 | 3.11+ works; the Docker image uses 3.12 |
| MySQL 8.0 | PostgreSQL is not supported by the current migrations |
| A domain and TLS | HTTPS is mandatory in production (`SESSION_COOKIE_SECURE=true`) |
| A WSGI server | `gunicorn` on Linux, `waitress` on Windows; both are in `requirements.txt` |

## 2. Create the database and a least-privilege user

Run this as a MySQL administrator, after replacing the password:

```powershell
mysql -u root -p < scripts/mysql_setup.sql
```

`scripts/mysql_setup.sql` creates `skilllink` (utf8mb4) and the `skilllink` user with
`SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, DROP` on that schema only.
The application never needs `GRANT`, `CREATE USER`, `FILE` or access to other schemas.

Verify:

```sql
SHOW GRANTS FOR 'skilllink'@'localhost';
```

## 3. Configure the environment

Copy `.env.example` to `.env` on the server (never commit it) and set at least:

| Variable | Production value |
|---|---|
| `APP_ENV` | `production` (enables the startup safety checks) |
| `SECRET_KEY` | 32+ random characters, e.g. `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | `mysql+pymysql://skilllink:PASSWORD@127.0.0.1:3306/skilllink?charset=utf8mb4` |
| `SESSION_COOKIE_SECURE` | `true` |
| `TRUSTED_PROXY_COUNT` | `1` when one reverse proxy (nginx, Caddy, a load balancer) terminates TLS |
| `MAIL_BACKEND` | `smtp` for real email, with `MAIL_SERVER`, `MAIL_DEFAULT_SENDER`, and credentials |
| `PAYMENT_PROVIDER` | `simulated` (M-Pesa Daraja is not implemented yet) |

The app refuses to start in production if the secret is short, the database is SQLite, secure
cookies are off, or SMTP is selected without a server and sender.

## 4. Install, migrate, create the administrator

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app wsgi.py db upgrade
flask --app wsgi.py create-admin you@example.com "Your Name"
```

Only the first administrator is created this way; further accounts are created by that
administrator in the app, and public registration cannot create admins.

## 5. Run the application server

Linux/macOS:

```bash
gunicorn --bind 0.0.0.0:8000 --workers 3 --threads 2 --timeout 60 \
  --access-logfile - --error-logfile - wsgi:app
```

Windows (gunicorn does not run there):

```powershell
waitress-serve --listen=0.0.0.0:8000 wsgi:app
```

`Procfile` contains the gunicorn command for platforms such as Render or Railway.

Check it is alive:

```bash
curl -fsS http://127.0.0.1:8000/health
# {"status":"ok","service":"SkillLink","database":"connected"}
```

## 6. Put HTTPS in front of it

Terminate TLS at a reverse proxy and forward to the app. With nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name skilllink.example.com;

    ssl_certificate     /etc/letsencrypt/live/skilllink.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/skilllink.example.com/privkey.pem;

    client_max_body_size 6m;          # uploads are capped at 5 MB by the app

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}

server {
    listen 80;
    server_name skilllink.example.com;
    return 301 https://$host$request_uri;
}
```

Set `TRUSTED_PROXY_COUNT=1` so the app believes exactly one hop of `X-Forwarded-For` and
`X-Forwarded-Proto`. Without it, rate limiting sees the proxy address and every user looks
like one client.

## 7. Uploads and static files

Uploads are written to `instance/uploads` and served by the application with a strict content
policy. On a container platform this directory must be a persistent volume, or replaced with
object storage (S3, Cloudflare R2, Azure Blob). Losing it loses profile and portfolio images.

## 8. Email

`MAIL_BACKEND=console` writes messages to the application log and is acceptable for a demo but
not for real users. For production set `MAIL_BACKEND=smtp` with:

```
MAIL_SERVER=smtp.example.com
MAIL_PORT=587
MAIL_USERNAME=...
MAIL_PASSWORD=...
MAIL_USE_TLS=true
MAIL_DEFAULT_SENDER=SkillLink <no-reply@skilllink.example.com>
```

Mail is queued in `email_outbox` and delivered after the request commits. Failures are stored
on the row; retry them with `flask --app wsgi.py send-emails --retry-failed`.

## 9. Docker alternative

```bash
docker compose up --build
docker compose exec web flask --app wsgi.py db upgrade
docker compose exec web flask --app wsgi.py create-admin you@example.com "Your Name"
```

The compose file starts MySQL 8 and the app, with volumes for the database and uploads.
For local plain-HTTP use it sets `SESSION_COOKIE_SECURE=false`; production must set it to `true`.

## 10. Backups and migrations

- Back up before every deploy: `mysqldump --single-transaction --routines --triggers skilllink > backup.sql`.
- Restore: `mysql skilllink < backup.sql`.
- Apply migrations with `flask --app wsgi.py db upgrade`.
- MySQL DDL is **not transactional**. If a migration fails halfway, the schema is partially
  changed; restore the backup and fix the migration rather than retrying blindly.
- Downgrading one release is supported. Downgrading across the escrow migration rewrites
  `released` transactions to `succeeded` and `held` transactions to `failed`, because the older
  schema has no escrow states. Downgrading to an empty schema drops every table and its data.

## 11. Post-deploy smoke test

1. `/health` returns `ok` and `database: connected`.
2. Register a client and a freelancer; both can sign in.
3. Client posts a job; freelancer applies; client hires.
4. Client funds the escrow; freelancer submits; client approves; the freelancer's earnings show
   the released amount.
5. The administrator sign-in works and `/admin/payments` lists the transaction.
6. `/settings/notifications` saves both columns.
7. Upload a profile image and confirm it survives a restart (or survives a redeploy when the
   volume or object storage is configured).

## 12. Security checklist before going live

- [ ] `APP_ENV=production`, `SECRET_KEY` is 32+ random characters, `.env` is not committed.
- [ ] HTTPS only; `SESSION_COOKIE_SECURE=true`; HSTS comes from the proxy or the app's header.
- [ ] `TRUSTED_PROXY_COUNT` matches the number of proxies in front of the app.
- [ ] MySQL user is limited to the `skilllink` schema.
- [ ] `MAIL_BACKEND=smtp` with a dedicated sender account.
- [ ] Database backups scheduled and restore tested.
- [ ] Uploads on persistent storage with a size limit.
- [ ] Logs collected; `/health` monitored.
