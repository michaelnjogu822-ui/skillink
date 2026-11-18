"""Project message polling, read markers and notification preferences."""
import secrets
import shutil
import unittest
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import (ClientProfile, FreelancerProfile, Job, JobApplication,
                        Message, Notification, NotificationPreference, Project,
                        User)
from app.services import notifications

PASSWORD = "secure-pass-2026"


def workspace_temp_dir(prefix):
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    path = base / f"{prefix}-{secrets.token_hex(4)}"
    path.mkdir()
    return path


class ConversationTestCase(unittest.TestCase):
    def setUp(self):
        self.upload_dir = workspace_temp_dir("messages")
        self.app = create_app(config_overrides=dict(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SECRET_KEY="test-secret-key",
            UPLOAD_FOLDER=str(self.upload_dir),
        ))
        with self.app.app_context():
            db.create_all()
            client = User(email="client@example.com", full_name="Client One", role="client")
            client.set_password(PASSWORD)
            client.client_profile = ClientProfile()
            freelancer = User(email="student@example.com", full_name="Student One", role="freelancer")
            freelancer.set_password(PASSWORD)
            freelancer.freelancer_profile = FreelancerProfile()
            outsider = User(email="outsider@example.com", full_name="Outsider", role="client")
            outsider.set_password(PASSWORD)
            outsider.client_profile = ClientProfile()
            admin = User(email="admin@example.com", full_name="Admin User", role="admin")
            admin.set_password(PASSWORD)
            db.session.add_all([client, freelancer, outsider, admin])
            db.session.flush()
            job = Job(client_id=client.id, title="Build a business website", category="Web",
                      description="A three page website for a hardware shop in Nairobi.", status="in_progress")
            db.session.add(job)
            db.session.flush()
            db.session.add(JobApplication(job_id=job.id, freelancer_id=freelancer.id,
                                          cover_letter="I build responsive websites.", status="accepted"))
            project = Project(job_id=job.id, client_id=client.id, freelancer_id=freelancer.id,
                              agreed_amount=15000, status="active")
            db.session.add(project)
            db.session.commit()
            self.project_id = project.id
            self.client_id = client.id
            self.freelancer_id = freelancer.id
        self.client = self.signed_in("client@example.com")
        self.freelancer = self.signed_in("student@example.com")
        self.outsider = self.signed_in("outsider@example.com")
        self.admin = self.signed_in("admin@example.com")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        shutil.rmtree(self.upload_dir, ignore_errors=True)

    def signed_in(self, email):
        browser = self.app.test_client()
        response = browser.post("/auth/login", data={"email": email, "password": PASSWORD})
        self.assertEqual(response.status_code, 302, f"could not sign in {email}")
        return browser

    def send(self, browser, body):
        response = browser.post(f"/projects/{self.project_id}/messages", data={"body": body},
                               follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return response

    def feed(self, browser, since_id=0):
        response = browser.get(f"/projects/{self.project_id}/messages.json?since_id={since_id}")
        return response

    def messages(self):
        with self.app.app_context():
            return Message.query.filter_by(project_id=self.project_id).order_by(Message.id).all()

    def notification_kinds(self, user_id):
        with self.app.app_context():
            return [row.kind for row in Notification.query.filter_by(user_id=user_id).all()]


class MessageFeedTests(ConversationTestCase):
    def test_feed_requires_a_signed_in_participant(self):
        anonymous = self.app.test_client().get(f"/projects/{self.project_id}/messages.json")
        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(self.feed(self.outsider).status_code, 403)

    def test_admin_can_read_the_conversation(self):
        self.send(self.freelancer, "Draft is ready for your review.")
        self.assertEqual(self.feed(self.admin).status_code, 200)

    def test_feed_returns_only_messages_after_since_id(self):
        self.send(self.freelancer, "First update.")
        self.send(self.freelancer, "Second update.")
        first_id = self.messages()[0].id

        response = self.feed(self.client, since_id=first_id)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([item["body"] for item in payload["messages"]], ["Second update."])
        self.assertEqual(payload["messages"][0]["sender"], "Student One")
        self.assertFalse(payload["messages"][0]["mine"])
        self.assertEqual(payload["latest_id"], self.messages()[1].id)

    def test_own_messages_are_marked_as_mine(self):
        self.send(self.freelancer, "Sent by the freelancer.")
        payload = self.feed(self.freelancer).get_json()
        self.assertTrue(payload["messages"][0]["mine"])

    def test_feed_reports_the_global_unread_count(self):
        self.send(self.freelancer, "One unread message.")
        self.assertEqual(self.feed(self.client).get_json()["unread_total"], 1)
        self.assertEqual(self.feed(self.freelancer).get_json()["unread_total"], 0)

    def test_reading_clears_the_unread_count(self):
        self.send(self.freelancer, "Please read this.")
        self.assertEqual(self.feed(self.client).get_json()["unread_total"], 1)

        response = self.client.post(f"/projects/{self.project_id}/messages/read")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["unread_total"], 0)
        self.assertEqual(self.feed(self.client).get_json()["unread_total"], 0)
        with self.app.app_context():
            self.assertTrue(all(row.read_at is not None for row in Message.query.all()))

    def test_outsider_cannot_mark_messages_read(self):
        self.send(self.freelancer, "Private message.")
        self.assertEqual(self.outsider.post(f"/projects/{self.project_id}/messages/read").status_code, 403)
        with self.app.app_context():
            self.assertIsNone(Message.query.one().read_at)


class NotificationPreferenceTests(ConversationTestCase):
    def test_defaults_are_all_enabled(self):
        page = self.client.get("/settings/notifications")
        self.assertEqual(page.status_code, 200)
        with self.app.app_context():
            values = notifications.preferences_for(self.client_id)
        self.assertEqual({flag for channels in values.values() for flag in channels.values()}, {True})
        self.assertIn(b'checked', page.data)

    def test_preferences_require_login(self):
        anonymous = self.app.test_client().get("/settings/notifications")
        self.assertEqual(anonymous.status_code, 302)

    def test_saving_preferences_persists_switches(self):
        response = self.client.post("/settings/notifications", data={
            "in_app_messages": "on", "email_messages": "on", "in_app_payments": "on"},
            follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            rows = {row.category: row for row in
                    NotificationPreference.query.filter_by(user_id=self.client_id).all()}
        self.assertTrue(rows["messages"].enabled)
        self.assertTrue(rows["messages"].email_enabled)
        self.assertTrue(rows["payments"].enabled)
        self.assertFalse(rows["payments"].email_enabled)
        self.assertFalse(rows["projects"].enabled)
        self.assertFalse(rows["projects"].email_enabled)
        self.assertFalse(rows["reviews"].enabled)

    def test_muted_category_stops_the_notification_but_not_the_message(self):
        self.client.post("/settings/notifications", data={}, follow_redirects=True)
        self.assertFalse(self.notification_kinds(self.client_id))

        self.send(self.freelancer, "You will see the message, not the bell.")
        self.assertEqual(len(self.messages()), 1)
        self.assertNotIn("message", self.notification_kinds(self.client_id))

    def test_enabled_category_still_notifies(self):
        self.send(self.freelancer, "This should raise the bell.")
        self.assertIn("message", self.notification_kinds(self.client_id))


class NotificationFormattingTests(ConversationTestCase):
    def test_message_text_is_built_in_one_place(self):
        self.assertEqual(
            notifications.format_message(
                "payment_released", currency="KES", amount="15,000.00", title="Build a website"),
            "KES 15,000.00 was released to you for Build a website.",
        )
        self.assertEqual(
            notifications.format_message("job_application", actor="Student One", title="Web design"),
            "Student One applied for Web design.",
        )

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            notifications.format_message("teleport", actor="x")

    def test_notify_respects_preferences_and_writes_the_row(self):
        with self.app.app_context():
            row = notifications.notify(self.client_id, "message", actor="Student One", title="Website")
            self.assertIsNotNone(row)
            notifications.set_preference(self.client_id, "messages", False)
            self.assertIsNone(notifications.notify(self.client_id, "message", actor="Student One", title="Website"))
            db.session.commit()
            self.assertEqual(Notification.query.filter_by(user_id=self.client_id).count(), 1)

    def test_application_notification_uses_the_shared_formatter(self):
        with self.app.app_context():
            job = Job(client_id=self.client_id, title="Logo and brand kit", category="Design",
                      description="Design a logo, colours and a small brand guide for a cafe.",
                      status="open", budget_min=5000, budget_max=8000)
            db.session.add(job)
            db.session.commit()
            job_id = job.id
        response = self.freelancer.post(f"/jobs/{job_id}/apply", data={
            "cover_letter": "I can design the full brand kit within two weeks.",
            "proposed_rate": "6000",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            note = Notification.query.filter_by(user_id=self.client_id, kind="job_application").one()
            self.assertEqual(note.message, "Student One applied for Logo and brand kit.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
