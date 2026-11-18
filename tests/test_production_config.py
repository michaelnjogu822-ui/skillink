"""Production configuration guards and reverse-proxy handling."""
import secrets
import shutil
import unittest
from pathlib import Path

from werkzeug.middleware.proxy_fix import ProxyFix

from app import create_app
from config import ProductionConfig


def workspace_temp_dir(prefix):
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    path = base / f"{prefix}-{secrets.token_hex(4)}"
    path.mkdir()
    return path


class ProductionGuardTests(unittest.TestCase):
    def setUp(self):
        self.upload_dir = workspace_temp_dir("prod")

    def tearDown(self):
        shutil.rmtree(self.upload_dir, ignore_errors=True)

    def build(self, **overrides):
        settings = {
            "SECRET_KEY": "s" * 48,
            "SQLALCHEMY_DATABASE_URI": "mysql+pymysql://skilllink:secret@127.0.0.1:3306/skilllink",
            "SESSION_COOKIE_SECURE": True,
            "UPLOAD_FOLDER": str(self.upload_dir),
        }
        settings.update(overrides)
        return create_app(config_object=ProductionConfig, config_overrides=settings)

    def test_a_complete_configuration_boots(self):
        app = self.build()
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["REMEMBER_COOKIE_SECURE"])
        self.assertEqual(app.config["PREFERRED_URL_SCHEME"], "https")

    def test_a_short_secret_is_rejected(self):
        with self.assertRaises(RuntimeError) as caught:
            self.build(SECRET_KEY="too-short")
        self.assertIn("SECRET_KEY", str(caught.exception))

    def test_sqlite_is_rejected(self):
        with self.assertRaises(RuntimeError) as caught:
            self.build(SQLALCHEMY_DATABASE_URI="sqlite:///instance/skilllink.db")
        self.assertIn("DATABASE_URL", str(caught.exception))

    def test_insecure_cookies_are_rejected(self):
        with self.assertRaises(RuntimeError) as caught:
            self.build(SESSION_COOKIE_SECURE=False)
        self.assertIn("SESSION_COOKIE_SECURE", str(caught.exception))

    def test_the_memory_mail_backend_is_rejected(self):
        with self.assertRaises(RuntimeError) as caught:
            self.build(MAIL_BACKEND="memory")
        self.assertIn("memory mail backend", str(caught.exception))

    def test_smtp_requires_a_server_and_a_sender(self):
        with self.assertRaises(RuntimeError) as caught:
            self.build(MAIL_BACKEND="smtp", MAIL_SERVER=None, MAIL_DEFAULT_SENDER=None)
        self.assertIn("SMTP", str(caught.exception))

    def test_console_mail_is_allowed_with_a_warning(self):
        app = self.build(MAIL_BACKEND="console")
        self.assertEqual(app.config["MAIL_BACKEND"], "console")


class ProxyConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.upload_dir = workspace_temp_dir("proxy")

    def tearDown(self):
        shutil.rmtree(self.upload_dir, ignore_errors=True)

    def build(self, proxy_count):
        return create_app(config_overrides=dict(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SECRET_KEY="test-secret-key",
            UPLOAD_FOLDER=str(self.upload_dir),
            TRUSTED_PROXY_COUNT=proxy_count,
        ))

    def test_proxy_fix_is_not_installed_by_default(self):
        self.assertNotIsInstance(self.build(0).wsgi_app, ProxyFix)

    def test_proxy_fix_is_installed_for_a_trusted_proxy(self):
        self.assertIsInstance(self.build(1).wsgi_app, ProxyFix)

    def test_forwarded_client_address_is_used_when_configured(self):
        app = self.build(1)
        seen = {}

        @app.get("/_test/client-address")
        def client_address():
            from flask import request
            seen["address"] = request.remote_addr
            return "ok"

        app.test_client().get("/_test/client-address", headers={"X-Forwarded-For": "198.51.100.7"})
        self.assertEqual(seen["address"], "198.51.100.7")

    def test_forwarded_header_is_ignored_without_a_trusted_proxy(self):
        app = self.build(0)
        seen = {}

        @app.get("/_test/client-address")
        def client_address():
            from flask import request
            seen["address"] = request.remote_addr
            return "ok"

        app.test_client().get("/_test/client-address", headers={"X-Forwarded-For": "198.51.100.7"})
        self.assertEqual(seen["address"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
