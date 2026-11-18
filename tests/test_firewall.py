"""Application firewall: scanning, injection, method and rate-limit rules."""
import secrets
import shutil
import time
import unittest
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import User

PASSWORD = "secure-pass-2026"


def workspace_temp_dir(prefix):
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    path = base / f"{prefix}-{secrets.token_hex(4)}"
    path.mkdir()
    return path


class FirewallTestCase(unittest.TestCase):
    def setUp(self):
        self.upload_dir = workspace_temp_dir("firewall")
        self.app = self.make_app()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        shutil.rmtree(self.upload_dir, ignore_errors=True)

    def make_app(self, **overrides):
        settings = dict(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SECRET_KEY="test-secret-key",
            UPLOAD_FOLDER=str(self.upload_dir),
        )
        settings.update(overrides)
        app = create_app(config_overrides=settings)
        with app.app_context():
            db.create_all()
        return app

    def blocks(self, app=None):
        return (app or self.app).extensions["firewall_log"].entries()

    def block_rules(self, app=None):
        return [entry.rule for entry in self.blocks(app)]


class ScannerTests(FirewallTestCase):
    def test_known_scanner_paths_are_refused_and_recorded(self):
        client = self.app.test_client()
        for path in ("/.env", "/.git/config", "/wp-admin", "/phpmyadmin/index.php",
                     "/backup.sql", "/config.ini", "/server-status", "/xmlrpc.php"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 404, path)
        self.assertIn("suspicious_path", self.block_rules())
        self.assertEqual(len(self.blocks()), 8)

    def test_path_traversal_is_refused(self):
        response = self.app.test_client().get("/..%2f..%2fetc/passwd")
        self.assertIn(response.status_code, (400, 404))
        self.assertTrue({"suspicious_path", "suspicious_query"} & set(self.block_rules()))

    def test_normal_marketplace_pages_are_not_blocked(self):
        client = self.app.test_client()
        for path in ("/", "/jobs", "/freelancers", "/auth/login", "/auth/register", "/health"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(self.blocks(), [])

    def test_a_php_file_anywhere_is_refused(self):
        self.assertEqual(self.app.test_client().get("/uploads/shell.php").status_code, 404)
        self.assertIn("suspicious_path", self.block_rules())


class InjectionTests(FirewallTestCase):
    def test_union_select_is_refused(self):
        response = self.app.test_client().get("/jobs", query_string={"q": "union select password from users"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("suspicious_query", self.block_rules())

    def test_tautology_payload_is_refused(self):
        response = self.app.test_client().get("/jobs?q=%27%20OR%20%271%27%3D%271")
        self.assertEqual(response.status_code, 400)
        self.assertIn("suspicious_query", self.block_rules())

    def test_script_tag_in_a_query_is_refused(self):
        response = self.app.test_client().get("/freelancers", query_string={"q": "<script>alert(1)</script>"})
        self.assertEqual(response.status_code, 400)

    def test_ordinary_searches_still_work(self):
        client = self.app.test_client()
        for term in ("web design", "select committee minutes", "python developer"):
            with self.subTest(term=term):
                self.assertEqual(client.get("/jobs", query_string={"q": term}).status_code, 200)
        self.assertEqual(self.blocks(), [])


class RequestShapeTests(FirewallTestCase):
    def test_unusual_methods_are_refused(self):
        response = self.app.test_client().open("/", method="TRACE")
        self.assertEqual(response.status_code, 405)
        self.assertIn("method_not_allowed", self.block_rules())

    def test_json_body_is_refused_on_a_form_endpoint(self):
        response = self.app.test_client().post(
            "/auth/login", data='{"email": "a@b.c"}', content_type="application/json")
        self.assertEqual(response.status_code, 415)
        self.assertIn("unsupported_content_type", self.block_rules())

    def test_form_submissions_are_allowed(self):
        response = self.app.test_client().post(
            "/auth/login", data={"email": "nobody@example.com", "password": "wrong-password"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.blocks(), [])

    def test_oversized_bodies_are_refused(self):
        app = self.make_app(MAX_CONTENT_LENGTH=512)
        response = app.test_client().post(
            "/auth/login", data={"email": "a@b.c", "password": "x" * 2000})
        self.assertEqual(response.status_code, 413)
        self.assertIn("oversized_body", self.block_rules(app))


class RateLimitTests(FirewallTestCase):
    def test_global_limit_blocks_then_recovers_after_the_window(self):
        app = self.make_app(RATE_LIMIT_GLOBAL=3, RATE_LIMIT_WINDOW_SECONDS=1)
        client = app.test_client()
        for _ in range(3):
            self.assertEqual(client.get("/jobs").status_code, 200)
        blocked = client.get("/jobs")
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)
        self.assertIn("rate_limit:global", self.block_rules(app))

        time.sleep(1.05)
        self.assertEqual(client.get("/jobs").status_code, 200)

    def test_sign_in_limit_is_tighter_than_the_global_limit(self):
        app = self.make_app(RATE_LIMIT_AUTH=2)
        client = app.test_client()
        payload = {"email": "nobody@example.com", "password": "wrong-password"}
        self.assertEqual(client.post("/auth/login", data=payload).status_code, 200)
        self.assertEqual(client.post("/auth/login", data=payload).status_code, 200)
        self.assertEqual(client.post("/auth/login", data=payload).status_code, 429)
        self.assertIn("rate_limit:auth", self.block_rules(app))

    def test_write_limit_applies_to_state_changing_requests(self):
        app = self.make_app(RATE_LIMIT_WRITE=2)
        client = app.test_client()
        for _ in range(2):
            client.post("/auth/logout")
        self.assertEqual(client.post("/auth/logout").status_code, 429)
        self.assertIn("rate_limit:write", self.block_rules(app))

    def test_health_checks_and_static_files_are_not_rate_limited(self):
        app = self.make_app(RATE_LIMIT_GLOBAL=1, RATE_LIMIT_WINDOW_SECONDS=60)
        client = app.test_client()
        for _ in range(5):
            self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/static/css/app.css").status_code, 200)
        self.assertEqual(self.blocks(app), [])

    def test_the_firewall_can_be_switched_off(self):
        app = self.make_app(FIREWALL_ENABLED=False, RATE_LIMIT_GLOBAL=1)
        client = app.test_client()
        for _ in range(5):
            self.assertEqual(client.get("/jobs").status_code, 200)
        self.assertEqual(self.blocks(app), [])


class HostHeaderTests(FirewallTestCase):
    def test_unknown_hostnames_are_refused_when_hosts_are_configured(self):
        app = self.make_app(ALLOWED_HOSTS=("skilllink.test",))
        client = app.test_client()
        self.assertEqual(client.get("/", headers={"Host": "evil.example"}).status_code, 400)
        self.assertIn("invalid_host", self.block_rules(app))
        self.assertEqual(client.get("/", headers={"Host": "skilllink.test"}).status_code, 200)

    def test_any_hostname_is_accepted_in_development(self):
        self.assertEqual(self.app.test_client().get("/", headers={"Host": "anything.local"}).status_code, 200)


class SecurityHeadersTests(FirewallTestCase):
    def test_defensive_headers_are_present(self):
        response = self.app.test_client().get("/")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(response.headers["X-Permitted-Cross-Domain-Policies"], "none")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertNotIn("Strict-Transport-Security", response.headers)

    def test_hsts_is_sent_over_https(self):
        response = self.app.test_client().get("/", base_url="https://localhost")
        self.assertIn("Strict-Transport-Security", response.headers)


class SecurityConsoleTests(FirewallTestCase):
    def sign_in_admin(self):
        with self.app.app_context():
            admin = User(email="admin@example.com", full_name="Admin User", role="admin")
            admin.set_password(PASSWORD)
            db.session.add(admin)
            db.session.commit()
        browser = self.app.test_client()
        response = browser.post("/auth/login", data={"email": "admin@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        return browser

    def test_administrators_can_review_rejections(self):
        self.app.test_client().get("/.env")
        browser = self.sign_in_admin()
        page = browser.get("/admin/security")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"suspicious_path", page.data)
        self.assertIn(b"/.env", page.data)

    def test_the_console_is_private_to_administrators(self):
        anonymous = self.app.test_client().get("/admin/security")
        self.assertEqual(anonymous.status_code, 302)
        with self.app.app_context():
            user = User(email="client@example.com", full_name="Client One", role="client")
            user.set_password(PASSWORD)
            db.session.add(user)
            db.session.commit()
        browser = self.app.test_client()
        browser.post("/auth/login", data={"email": "client@example.com", "password": PASSWORD})
        self.assertEqual(browser.get("/admin/security").status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
