"""Transactional email: queueing, the outbox, preferences and retries."""
import secrets
import shutil
import unittest
from pathlib import Path
from unittest import mock

from app import create_app
from app.extensions import db
from app.models import (ClientProfile, EmailOutbox, FreelancerProfile, Job,
                        JobApplication, Notification, Project, User)
from app.services import email, escrow, notifications

PASSWORD = "secure-pass-2026"


def workspace_temp_dir(prefix):
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    path = base / f"{prefix}-{secrets.token_hex(4)}"
    path.mkdir()
    return path


class EmailTestCase(unittest.TestCase):
    def setUp(self):
        self.upload_dir = workspace_temp_dir("email")
        self.app = create_app(config_overrides=dict(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SECRET_KEY="test-secret-key",
            UPLOAD_FOLDER=str(self.upload_dir),
            MAIL_BACKEND="memory",
        ))
        with self.app.app_context():
            db.create_all()
            self.client_id = self.add_user("client@example.com", "Client One", "client", ClientProfile)
            self.freelancer_id = self.add_user("student@example.com", "Student One", "freelancer", FreelancerProfile)
            db.session.commit()
        self.client = self.signed_in("client@example.com")
        self.freelancer = self.signed_in("student@example.com")

    def add_user(self, email_address, name, role, profile_class):
        user = User(email=email_address, full_name=name, role=role)
        user.set_password(PASSWORD)
        setattr(user, "client_profile" if role == "client" else "freelancer_profile", profile_class())
        db.session.add(user)
        db.session.flush()
        return user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        shutil.rmtree(self.upload_dir, ignore_errors=True)

    def signed_in(self, email_address):
        browser = self.app.test_client()
        response = browser.post("/auth/login", data={"email": email_address, "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        return browser

    def open_job(self, title="Build a business website"):
        with self.app.app_context():
            job = Job(client_id=self.client_id, title=title, category="Web", status="open",
                      description="A three page website for a hardware shop in Nairobi.",
                      budget_min=15000, budget_max=15000)
            db.session.add(job)
            db.session.commit()
            return job.id

    def apply(self, job_id, browser=None):
        return (browser or self.freelancer).post(f"/jobs/{job_id}/apply", data={
            "cover_letter": "I build responsive websites and can start this week.",
            "proposed_rate": "15000",
        }, follow_redirects=True)

    def outbox(self):
        with self.app.app_context():
            return EmailOutbox.query.order_by(EmailOutbox.id).all()

    def sent_emails(self):
        return self.app.extensions.get("sent_emails", [])

    def job_with_application(self, job_status="open", application_status="pending"):
        """A job plus one application, without a project (the hire route creates that)."""
        with self.app.app_context():
            job = Job(client_id=self.client_id, title="Build a business website", category="Web",
                      status=job_status, description="A three page website in Nairobi.",
                      budget_min=15000, budget_max=15000)
            db.session.add(job)
            db.session.flush()
            application = JobApplication(job_id=job.id, freelancer_id=self.freelancer_id,
                                         cover_letter="I can build this.", status=application_status)
            db.session.add(application)
            db.session.commit()
            return job.id, application.id

    def project_with_parties(self, status="active"):
        with self.app.app_context():
            job = Job(client_id=self.client_id, title="Build a business website", category="Web",
                      status="in_progress", description="A three page website in Nairobi.",
                      budget_min=15000, budget_max=15000)
            db.session.add(job)
            db.session.flush()
            application = JobApplication(job_id=job.id, freelancer_id=self.freelancer_id,
                                         cover_letter="I can build this.", status="accepted")
            db.session.add(application)
            project = Project(job_id=job.id, client_id=self.client_id,
                              freelancer_id=self.freelancer_id, agreed_amount=15000, status=status)
            db.session.add(project)
            db.session.commit()
            return job.id, application.id, project.id


class QueueAndDeliveryTests(EmailTestCase):
    def test_applying_emails_the_client_and_records_a_sent_message(self):
        job_id = self.open_job()
        response = self.apply(job_id)
        self.assertEqual(response.status_code, 200)

        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].recipient, "client@example.com")
        self.assertEqual(rows[0].status, "sent")
        self.assertIsNotNone(rows[0].sent_at)
        self.assertEqual(rows[0].attempts, 0)
        self.assertIn("Build a business website", rows[0].subject)

        self.assertEqual(len(self.sent_emails()), 1)
        body = self.sent_emails()[0].body
        self.assertIn("Student One", body)
        self.assertIn("Build a business website", body)
        self.assertIn("/applications", body)

    def test_hiring_emails_the_freelancer(self):
        _job_id, application_id = self.job_with_application()
        response = self.client.post(f"/applications/{application_id}/hire", data={}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].recipient, "student@example.com")
        self.assertIn("You were hired", rows[0].subject)
        self.assertIn("15,000.00", rows[0].body)

    def test_work_approval_emails_the_release(self):
        job_id, _application_id, project_id = self.project_with_parties()
        self.client.post(f"/projects/{project_id}/fund", data={}, follow_redirects=True)
        self.freelancer.post(f"/projects/{project_id}/submit", data={}, follow_redirects=True)
        self.client.post(f"/projects/{project_id}/complete", data={}, follow_redirects=True)

        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertIn("Work approved", rows[0].subject)
        self.assertIn("Released amount: KES 15,000.00", rows[0].body)

    def test_cancellation_email_explains_the_refund(self):
        _job_id, _application_id, project_id = self.project_with_parties()
        self.client.post(f"/projects/{project_id}/fund", data={}, follow_redirects=True)
        self.client.post(f"/projects/{project_id}/cancel", data={}, follow_redirects=True)

        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertIn("Project cancelled", rows[0].subject)
        self.assertIn("refunded to the client", rows[0].body)

    def test_unknown_event_is_rejected(self):
        with self.app.app_context():
            user = db.session.get(User, self.client_id)
            with self.assertRaises(ValueError):
                email.queue(user, "teleport", title="x")


class EmailPreferenceTests(EmailTestCase):
    def test_disabling_email_for_a_category_stops_the_message(self):
        with self.app.app_context():
            notifications.set_preference(self.client_id, "applications", False, channel="email")
            db.session.commit()
        self.apply(self.open_job())
        self.assertEqual(self.outbox(), [])
        with self.app.app_context():
            self.assertEqual(Notification.query.filter_by(user_id=self.client_id, kind="job_application").count(), 1)

    def test_muting_in_app_does_not_stop_email(self):
        with self.app.app_context():
            notifications.set_preference(self.client_id, "applications", False, channel="in_app")
            db.session.commit()
        self.apply(self.open_job())
        self.assertEqual(len(self.outbox()), 1)
        with self.app.app_context():
            self.assertEqual(Notification.query.filter_by(user_id=self.client_id).count(), 0)


class OutboxRetryTests(EmailTestCase):
    def failing_backend(self):
        backend = mock.Mock()
        backend.send.side_effect = RuntimeError("SMTP refused the connection")
        return backend

    def test_failure_is_recorded_and_a_later_run_retries(self):
        job_id = self.open_job()
        with mock.patch.object(email, "backend", return_value=self.failing_backend()):
            self.apply(job_id)

        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "failed")
        self.assertEqual(rows[0].attempts, 1)
        self.assertIn("SMTP refused", rows[0].last_error)
        self.assertEqual(self.sent_emails(), [])

        with self.app.app_context():
            sent, failed = email.flush(include_failed=True)
        self.assertEqual((sent, failed), (1, 0))
        self.assertEqual(self.outbox()[0].status, "sent")
        self.assertEqual(len(self.sent_emails()), 1)

    def test_a_message_is_not_retried_forever(self):
        with self.app.app_context():
            db.session.add(EmailOutbox(user_id=self.client_id, recipient="client@example.com",
                                       subject="Old message", body="Body", status="failed",
                                       attempts=email.MAX_ATTEMPTS))
            db.session.commit()
        with self.app.app_context():
            sent, failed = email.flush(include_failed=True)
        self.assertEqual((sent, failed), (0, 1))
        self.assertIn("Gave up", self.outbox()[0].last_error)

    def test_cli_command_sends_pending_mail(self):
        with self.app.app_context():
            db.session.add(EmailOutbox(user_id=self.client_id, recipient="client@example.com",
                                       subject="Queued from another process", body="Body", status="pending"))
            db.session.commit()
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["send-emails"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("1 sent", result.output)
        self.assertEqual(self.outbox()[0].status, "sent")

    def test_smtp_backend_requires_configuration(self):
        with self.app.app_context():
            self.app.config["MAIL_BACKEND"] = "smtp"
            self.app.config["MAIL_SERVER"] = None
            with self.assertRaises(RuntimeError):
                email.backend()


class EscrowEmailTests(EmailTestCase):
    def test_funding_does_not_send_an_email_but_release_does(self):
        _job_id, _application_id, project_id = self.project_with_parties()
        self.client.post(f"/projects/{project_id}/fund", data={}, follow_redirects=True)
        self.assertEqual(self.outbox(), [])

        self.freelancer.post(f"/projects/{project_id}/submit", data={}, follow_redirects=True)
        self.client.post(f"/projects/{project_id}/complete", data={}, follow_redirects=True)
        self.assertEqual(len(self.outbox()), 1)
        self.assertEqual(self.outbox()[0].recipient, "student@example.com")


if __name__ == "__main__":
    unittest.main(verbosity=2)
