"""Sign-in hardening: lockout, per-account scoping, session reset, upload path."""
import os
import secrets
import shutil
import unittest
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import FreelancerProfile, User

PASSWORD = "secure-pass-2026"
WRONG = "not-the-password"
MAX_FAILURES = 3


def workspace_temp_dir(prefix):
    """Create a throwaway directory inside the project.

    `tempfile.TemporaryDirectory` is avoided on purpose: some sandboxed
    environments create it with permissions that later writes cannot use.
    """
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    path = base / f"{prefix}-{secrets.token_hex(4)}"
    path.mkdir()
    return path


class LoginSecurityTest(unittest.TestCase):
    def setUp(self):
        self.upload_dir = workspace_temp_dir("login")
        self.app = create_app(config_overrides=dict(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SECRET_KEY="test-secret-key",
            UPLOAD_FOLDER=str(self.upload_dir),
            LOGIN_MAX_FAILURES=MAX_FAILURES,
            LOGIN_WINDOW_SECONDS=300,
        ))
        with self.app.app_context():
            db.create_all()
            for email, name in (("student@example.com", "Student One"), ("other@example.com", "Other Student")):
                user = User(email=email, full_name=name, role="freelancer")
                user.set_password(PASSWORD)
                user.freelancer_profile = FreelancerProfile()
                db.session.add(user)
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        shutil.rmtree(self.upload_dir, ignore_errors=True)

    def sign_in(self, email="student@example.com", password=PASSWORD, client=None):
        return (client or self.client).post(
            "/auth/login", data={"email": email, "password": password}
        )

    def fail_login(self, times=1, email="student@example.com"):
        response = None
        for _ in range(times):
            response = self.sign_in(email=email, password=WRONG)
        return response

    def test_valid_credentials_reach_the_dashboard(self):
        response = self.sign_in()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_repeated_failures_block_even_the_correct_password(self):
        response = self.fail_login(MAX_FAILURES)
        self.assertEqual(response.status_code, 200, "attempts below the limit must not lock the account")

        blocked = self.sign_in()
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)
        self.assertIn(b"Too many failed sign-in attempts", blocked.data)
        self.assertEqual(self.client.get("/dashboard").status_code, 302, "a blocked attempt must not create a session")

    def test_lockout_is_scoped_to_one_email_address(self):
        self.fail_login(MAX_FAILURES)
        self.assertEqual(self.sign_in().status_code, 429)

        other_client = self.app.test_client()
        self.assertEqual(self.sign_in(email="other@example.com", client=other_client).status_code, 302)

    def test_lockout_is_scoped_to_one_client_address(self):
        self.fail_login(MAX_FAILURES)
        self.assertEqual(self.sign_in().status_code, 429)

        elsewhere = self.app.test_client()
        response = elsewhere.post(
            "/auth/login",
            data={"email": "student@example.com", "password": PASSWORD},
            environ_base={"REMOTE_ADDR": "203.0.113.9"},
        )
        self.assertEqual(response.status_code, 302)

    def test_successful_login_clears_the_failure_count(self):
        self.fail_login(MAX_FAILURES - 1)
        self.assertNotEqual(self.sign_in().status_code, 429)
        self.client.post("/auth/logout")
        self.fail_login(MAX_FAILURES - 1)
        self.assertNotEqual(self.sign_in().status_code, 429, "failures before a success must not count again")

    def test_unknown_email_is_handled_like_a_wrong_password(self):
        response = self.sign_in(email="nobody@example.com", password=WRONG)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"sign you in with those details", response.data)
        self.fail_login(MAX_FAILURES - 1, email="nobody@example.com")
        self.assertEqual(self.sign_in(email="nobody@example.com", password=WRONG).status_code, 429)

    def test_login_replaces_a_planted_session(self):
        with self.client.session_transaction() as session:
            session["planted"] = "attacker-controlled"
        self.assertEqual(self.sign_in().status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("planted", session)
            self.assertIn("_user_id", session)

    def test_upload_folder_is_resolved_to_an_absolute_path(self):
        self.assertTrue(os.path.isabs(self.app.config["UPLOAD_FOLDER"]))
        self.assertEqual(self.app.config["UPLOAD_FOLDER"], str(self.upload_dir))


if __name__ == "__main__":
    unittest.main(verbosity=2)
