"""Escrow payment lifecycle: fund, release, refund, and ledger access."""
import secrets
import shutil
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (ClientProfile, FreelancerProfile, Job, JobApplication,
                        Project, Transaction, User)
from app.services import escrow

PASSWORD = "secure-pass-2026"
AMOUNT = Decimal("15000.00")


def workspace_temp_dir(prefix):
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    path = base / f"{prefix}-{secrets.token_hex(4)}"
    path.mkdir()
    return path


class EscrowTestCase(unittest.TestCase):
    def setUp(self):
        self.upload_dir = workspace_temp_dir("escrow")
        self.app = create_app(config_overrides=dict(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SECRET_KEY="test-secret-key",
            UPLOAD_FOLDER=str(self.upload_dir),
            PAYMENT_PROVIDER="simulated",
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
                      description="A three page website for a hardware shop in Nairobi.",
                      status="in_progress", budget_min=AMOUNT, budget_max=AMOUNT)
            db.session.add(job)
            db.session.flush()
            db.session.add(JobApplication(job_id=job.id, freelancer_id=freelancer.id,
                                          cover_letter="I build responsive websites.", status="accepted"))
            project = Project(job_id=job.id, client_id=client.id, freelancer_id=freelancer.id,
                              agreed_amount=AMOUNT, status="active")
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

    def post(self, browser, path, follow=True):
        return browser.post(path, data={}, follow_redirects=follow)

    def project_url(self):
        return f"/projects/{self.project_id}"

    def escrow_rows(self):
        with self.app.app_context():
            return Transaction.query.filter_by(project_id=self.project_id, kind="escrow").all()

    def project(self):
        """A plain snapshot, because ORM objects are detached once the context closes."""
        with self.app.app_context():
            project = db.session.get(Project, self.project_id)
            return {
                "status": project.status,
                "completed_at": project.completed_at,
                "job_status": project.job.status,
                "agreed_amount": project.agreed_amount,
            }

    def fund(self, browser=None):
        return self.post(browser or self.client, f"{self.project_url()}/fund")


class FundingTests(EscrowTestCase):
    def test_client_funding_holds_the_agreed_amount(self):
        response = self.fund()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Held in escrow", response.data)

        rows = self.escrow_rows()
        self.assertEqual(len(rows), 1)
        transaction = rows[0]
        self.assertEqual(transaction.status, "held")
        self.assertEqual(transaction.amount, AMOUNT)
        self.assertEqual(transaction.payer_id, self.client_id)
        self.assertEqual(transaction.payee_id, self.freelancer_id)
        self.assertEqual(transaction.currency, "KES")
        self.assertEqual(transaction.provider, "simulated")
        self.assertTrue(transaction.provider_reference.startswith("SIM-DEP-"))

    def test_funding_twice_is_rejected_and_keeps_one_record(self):
        self.fund()
        response = self.fund()
        self.assertIn(b"already funded", response.data)
        self.assertEqual(len(self.escrow_rows()), 1)

    def test_only_the_client_can_fund(self):
        self.assertEqual(self.fund(self.freelancer).status_code, 403)
        self.assertEqual(self.fund(self.outsider).status_code, 403)
        self.assertEqual(self.escrow_rows(), [])

    def test_cancelled_project_cannot_be_funded(self):
        self.post(self.client, f"{self.project_url()}/cancel")
        response = self.fund()
        self.assertIn(b"closed", response.data)
        self.assertEqual(self.escrow_rows(), [])

    def test_transaction_status_is_checked_by_the_database(self):
        with self.app.app_context():
            db.session.add(Transaction(project_id=self.project_id, payer_id=self.client_id,
                                       payee_id=self.freelancer_id, amount=10,
                                       status="invented", provider="simulated"))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()


class CompletionTests(EscrowTestCase):
    def test_work_cannot_be_approved_before_the_escrow_is_funded(self):
        self.assertEqual(self.post(self.freelancer, f"{self.project_url()}/submit").status_code, 200)
        response = self.post(self.client, f"{self.project_url()}/complete")
        self.assertIn(b"Fund the escrow", response.data)
        self.assertEqual(self.project()["status"], "submitted")
        self.assertEqual(self.escrow_rows(), [])

    def test_approval_releases_the_held_amount(self):
        self.fund()
        self.post(self.freelancer, f"{self.project_url()}/submit")
        response = self.post(self.client, f"{self.project_url()}/complete")
        self.assertEqual(response.status_code, 200)

        transaction = self.escrow_rows()[0]
        self.assertEqual(transaction.status, "released")
        self.assertTrue(transaction.settlement_reference.startswith("SIM-PAY-"))
        self.assertNotEqual(transaction.settlement_reference, transaction.provider_reference)
        project = self.project()
        self.assertEqual(project["status"], "completed")
        self.assertIsNotNone(project["completed_at"])
        self.assertEqual(project["job_status"], "completed")

    def test_released_payment_counts_as_freelancer_earnings(self):
        self.fund()
        self.post(self.freelancer, f"{self.project_url()}/submit")
        self.post(self.client, f"{self.project_url()}/complete")
        earnings = self.freelancer.get("/earnings")
        self.assertIn(b"Released", earnings.data)
        self.assertIn(b"15,000.00", earnings.data)
        dashboard = self.freelancer.get("/dashboard")
        self.assertIn(b"15,000.00", dashboard.data)

    def test_held_payment_is_not_counted_as_earned_yet(self):
        self.fund()
        dashboard = self.freelancer.get("/dashboard")
        self.assertIn(b"No payments yet", dashboard.data)

    def test_freelancer_cannot_approve_work(self):
        self.fund()
        self.post(self.freelancer, f"{self.project_url()}/submit")
        self.assertEqual(self.post(self.freelancer, f"{self.project_url()}/complete").status_code, 403)
        self.assertEqual(self.project()["status"], "submitted")


class CancellationTests(EscrowTestCase):
    def test_cancelling_a_funded_project_refunds_the_client(self):
        self.fund()
        response = self.post(self.client, f"{self.project_url()}/cancel")
        self.assertEqual(response.status_code, 200)
        transaction = self.escrow_rows()[0]
        self.assertEqual(transaction.status, "refunded")
        self.assertTrue(transaction.settlement_reference.startswith("SIM-RFD-"))
        project = self.project()
        self.assertEqual(project["status"], "cancelled")
        self.assertEqual(project["job_status"], "cancelled")
        spending = self.client.get("/earnings")
        self.assertIn(b"Refunded", spending.data)

    def test_refund_does_not_count_as_earnings(self):
        self.fund()
        self.post(self.client, f"{self.project_url()}/cancel")
        dashboard = self.freelancer.get("/dashboard")
        self.assertIn(b"No payments yet", dashboard.data)

    def test_cancelling_without_funding_creates_no_transaction(self):
        self.post(self.client, f"{self.project_url()}/cancel")
        self.assertEqual(self.project()["status"], "cancelled")
        self.assertEqual(self.escrow_rows(), [])

    def test_freelancer_cannot_cancel_the_project(self):
        self.fund()
        self.assertEqual(self.post(self.freelancer, f"{self.project_url()}/cancel").status_code, 403)
        self.assertEqual(self.project()["status"], "active")
        self.assertEqual(self.escrow_rows()[0].status, "held")

    def test_completed_project_cannot_be_cancelled(self):
        self.fund()
        self.post(self.freelancer, f"{self.project_url()}/submit")
        self.post(self.client, f"{self.project_url()}/complete")
        self.assertEqual(self.post(self.client, f"{self.project_url()}/cancel").status_code, 409)


class LedgerTests(EscrowTestCase):
    def test_participants_see_the_payment_for_their_project(self):
        self.fund()
        for browser in (self.client, self.freelancer):
            page = browser.get("/earnings")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Build a business website", page.data)
            self.assertIn(b"SIM-DEP-", page.data)

    def test_admin_sees_every_payment(self):
        self.fund()
        ledger = self.admin.get("/admin/payments")
        self.assertEqual(ledger.status_code, 200)
        self.assertIn(b"Build a business website", ledger.data)
        self.assertIn(b"Client One", ledger.data)
        self.assertIn(b"Student One", ledger.data)
        self.assertIn(b"Held in escrow", ledger.data)

    def test_non_admins_cannot_open_the_platform_ledger(self):
        for browser in (self.client, self.freelancer):
            self.assertEqual(browser.get("/admin/payments").status_code, 403)


class SimulatedProviderTests(unittest.TestCase):
    def test_receipts_are_unique_and_identify_the_provider(self):
        provider = escrow.SimulatedProvider()
        receipts = [
            provider.charge(project=None, payer=None),
            provider.payout(project=None, transaction=None),
            provider.refund(project=None, transaction=None),
        ]
        self.assertEqual(len({receipt.reference for receipt in receipts}), 3)
        self.assertTrue(all(receipt.provider == "simulated" for receipt in receipts))
        self.assertTrue(receipts[0].reference.startswith("SIM-DEP-"))
        self.assertTrue(receipts[1].reference.startswith("SIM-PAY-"))
        self.assertTrue(receipts[2].reference.startswith("SIM-RFD-"))

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(escrow.PaymentError):
            escrow.get_provider("mpesa-not-configured")


if __name__ == "__main__":
    unittest.main(verbosity=2)
