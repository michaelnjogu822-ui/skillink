"""Integration coverage for the primary client-to-freelancer workflow."""
import unittest
from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from app import create_app
from app.extensions import db
from app.models import (ClientProfile, FreelancerProfile, FreelancerSkill, Job,
                        JobApplication, Message, Notification, Project, Report,
                        Skill, Transaction, User)
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable


class MarketplaceWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.upload_dir = TemporaryDirectory()
        self.app = create_app(config_overrides=dict(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SECRET_KEY="test-secret-key",
            UPLOAD_FOLDER=self.upload_dir.name,
        ))
        with self.app.app_context():
            db.create_all()
        self.client_browser = self.app.test_client()
        self.freelancer_browser = self.app.test_client()
        self.outsider_browser = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.upload_dir.cleanup()

    def register(self, browser, full_name, email, role):
        return self.post_with_csrf(browser, "/auth/register", {
            "full_name": full_name,
            "email": email,
            "role": role,
            "password": "secure-pass-2026",
            "confirm_password": "secure-pass-2026",
        }, follow_redirects=True)

    def post_with_csrf(self, browser, path, data, **kwargs):
        page = browser.get("/auth/login", follow_redirects=True)
        token_match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data)
        self.assertIsNotNone(token_match, "Expected a CSRF token on the form page")
        payload = dict(data)
        payload["csrf_token"] = token_match.group(1).decode()
        return browser.post(path, data=payload, **kwargs)

    def test_job_application_project_messages_completion_and_review(self):
        response = self.register(self.client_browser, "Client One", "client@example.com", "client")
        self.assertEqual(response.status_code, 200)
        response = self.register(self.freelancer_browser, "Student One", "student@example.com", "freelancer")
        self.assertEqual(response.status_code, 200)
        self.register(self.outsider_browser, "Other Client", "other@example.com", "client")
        self.assertEqual(self.client_browser.get("/dashboard").status_code, 200)
        self.assertEqual(self.freelancer_browser.get("/dashboard").status_code, 200)
        self.assertEqual(self.client_browser.get("/profile").status_code, 200)
        self.assertEqual(self.freelancer_browser.get("/profile").status_code, 200)
        response = self.post_with_csrf(self.freelancer_browser, "/profile", {
            "full_name": "Student One",
            "location": "Nairobi",
            "headline": "Junior web developer",
            "bio": "I build responsive sites and help small teams launch online.",
            "university": "University of Nairobi",
            "hourly_rate": "1000",
            "skills": "Python, Web design",
            "profile_image": (BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "profile.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            student = User.query.filter_by(email="student@example.com").one()
            image_name = student.freelancer_profile.profile_image
            self.assertTrue(Path(self.upload_dir.name, image_name).is_file())
            self.assertEqual({link.skill.name for link in student.freelancer_profile.skills}, {"Python", "Web design"})
        response = self.post_with_csrf(self.freelancer_browser, "/portfolio/new", {
            "title": "Student portfolio",
            "description": "A responsive site for a local organization.",
            "project_url": "https://example.org/project",
            "image": (BytesIO(b"\x89PNG\r\n\x1a\nportfolio-image"), "portfolio.png"),
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            from app.models import Portfolio
            portfolio = db.session.query(Portfolio).one()
            image_name = portfolio.image_path
            self.assertTrue(Path(self.upload_dir.name, image_name).is_file())
            portfolio_id = portfolio.id
        response = self.post_with_csrf(self.freelancer_browser, f"/portfolio/{portfolio_id}/delete", {}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Path(self.upload_dir.name, image_name).exists())

        response = self.post_with_csrf(self.client_browser, "/jobs/new", {
            "title": "Build a portfolio website",
            "category": "Development",
            "description": "Create a responsive portfolio website with three content pages and a contact form.",
            "budget_min": "10000",
            "budget_max": "20000",
            "location": "Nairobi",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            job_id = db.session.query(Job).one().id

        self.assertEqual(self.freelancer_browser.get("/jobs?q=portfolio").status_code, 200)
        self.assertIn(b"Build a portfolio website", self.freelancer_browser.get("/jobs?q=portfolio").data)
        response = self.post_with_csrf(self.freelancer_browser, f"/jobs/{job_id}/apply", {
            "cover_letter": "I have built several responsive websites and can deliver this project with careful attention to usability.",
            "proposed_rate": "15000",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        response = self.client_browser.get("/applications")
        self.assertEqual(response.status_code, 200)
        response = self.post_with_csrf(self.client_browser, "/applications/1/hire", {}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            project_id = db.session.query(Project).one().id
        self.assertEqual(self.outsider_browser.get(f"/projects/{project_id}").status_code, 403)
        response = self.post_with_csrf(self.outsider_browser, f"/projects/{project_id}/messages", {
            "body": "I should not be able to access this project conversation."
        })
        self.assertEqual(response.status_code, 403)

        response = self.post_with_csrf(self.freelancer_browser, f"/projects/{project_id}/messages", {
            "body": "I have started the first draft and will share progress shortly."
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        inbox = self.client_browser.get("/messages")
        self.assertEqual(inbox.status_code, 200)
        self.assertIn(b"1 unread", inbox.data)
        self.assertEqual(self.client_browser.get(f"/projects/{project_id}").status_code, 200)
        with self.app.app_context():
            message = Message.query.filter_by(project_id=project_id).one()
            self.assertIsNotNone(message.read_at)
        response = self.post_with_csrf(self.client_browser, f"/projects/{project_id}/complete", {})
        self.assertEqual(response.status_code, 409)
        response = self.post_with_csrf(self.freelancer_browser, f"/projects/{project_id}/submit", {}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        unfunded = self.post_with_csrf(self.client_browser, f"/projects/{project_id}/complete", {}, follow_redirects=True)
        self.assertIn(b"Fund the escrow", unfunded.data)
        funded = self.post_with_csrf(self.client_browser, f"/projects/{project_id}/fund", {}, follow_redirects=True)
        self.assertIn(b"Held in escrow", funded.data)
        response = self.post_with_csrf(self.client_browser, f"/projects/{project_id}/complete", {}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        response = self.post_with_csrf(self.client_browser, f"/projects/{project_id}/review", {
            "rating": "5",
            "comment": "Excellent communication and careful work.",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            project = db.session.get(Project, project_id)
            freelancer = User.query.filter_by(email="student@example.com").one()
            client = User.query.filter_by(email="client@example.com").one()
            self.assertEqual(project.status, "completed")
            self.assertIsNotNone(project.submitted_at)
            self.assertIsNotNone(project.completed_at)
            self.assertEqual(freelancer.freelancer_profile.review_count, 1)
            self.assertEqual(float(freelancer.freelancer_profile.average_rating), 5.0)
            self.assertEqual(Notification.query.filter_by(user_id=freelancer.id, kind="review_received").count(), 1)
            db.session.add_all([
                Transaction(project_id=project.id, payer_id=client.id, payee_id=freelancer.id,
                            amount=15000, currency="KES", status="succeeded", provider="test"),
                Transaction(project_id=project.id, payer_id=client.id, payee_id=freelancer.id,
                            amount=300, currency="USD", status="pending", provider="test"),
            ])
            db.session.commit()
            freelancer_id, client_id = freelancer.id, client.id
        earnings_page = self.freelancer_browser.get("/earnings")
        self.assertEqual(earnings_page.status_code, 200)
        self.assertIn(b"KES", earnings_page.data)
        self.assertIn(b"15,000.00", earnings_page.data)
        self.assertIn(b"USD", earnings_page.data)
        client_spending = self.client_browser.get("/earnings")
        self.assertEqual(client_spending.status_code, 200)
        self.assertIn(b"Project spending", client_spending.data)
        self.assertEqual(self.outsider_browser.get("/earnings").status_code, 200)
        self.assertEqual(self.freelancer_browser.get(f"/freelancers/{freelancer_id}").status_code, 200)
        self.assertEqual(self.client_browser.get(f"/notifications/{1}/open").status_code, 302)
        self.assertEqual(self.freelancer_browser.get(f"/freelancers/{freelancer.id}").status_code, 200)

    def test_roles_protect_client_job_creation(self):
        self.register(self.freelancer_browser, "Student Two", "student2@example.com", "freelancer")
        response = self.freelancer_browser.get("/jobs/new")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.freelancer_browser.get("/applications").status_code, 403)
        self.assertEqual(self.freelancer_browser.get("/admin/users").status_code, 403)
        self.register(self.client_browser, "Client Two", "client2@example.com", "client")
        self.assertEqual(self.client_browser.get("/my-applications").status_code, 403)

    def test_registration_login_duplicate_email_and_password_validation(self):
        self.assertEqual(self.client_browser.get("/dashboard").status_code, 302)
        response = self.post_with_csrf(self.client_browser, "/auth/register", {
            "full_name": "Authentication Client", "email": "auth@example.com", "role": "client",
            "password": "weak", "confirm_password": "different",
        })
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(User.query.count(), 0)

        response = self.register(self.client_browser, "Authentication Client", "auth@example.com", "client")
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            user = User.query.filter_by(email="auth@example.com").one()
            user_id = user.id
            self.assertTrue(user.check_password("secure-pass-2026"))
            self.assertNotEqual(user.password_hash, "secure-pass-2026")
            self.assertIs(user.client_profile.user, user)
            self.assertIsNone(user.freelancer_profile)
        self.post_with_csrf(self.client_browser, "/auth/logout", {}, follow_redirects=True)

        response = self.post_with_csrf(self.client_browser, "/auth/login", {
            "email": "AUTH@example.com", "password": "incorrect-password",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client_browser.get("/dashboard").status_code, 302)
        response = self.post_with_csrf(self.client_browser, "/auth/login", {
            "email": "AUTH@example.com", "password": "secure-pass-2026",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client_browser.get("/dashboard").status_code, 200)

        self.post_with_csrf(self.client_browser, "/auth/logout", {}, follow_redirects=True)
        response = self.post_with_csrf(self.client_browser, "/auth/register", {
            "full_name": "Duplicate User", "email": "auth@example.com", "role": "freelancer",
            "password": "secure-pass-2026", "confirm_password": "secure-pass-2026",
        })
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(User.query.count(), 1)
            self.assertEqual(db.session.get(User, user_id).role, "client")

    def test_database_relationships_and_application_cascade(self):
        self.register(self.client_browser, "Relationship Client", "relation-client@example.com", "client")
        self.register(self.freelancer_browser, "Relationship Student", "relation-freelancer@example.com", "freelancer")
        with self.app.app_context():
            client = User.query.filter_by(email="relation-client@example.com").one()
            freelancer = User.query.filter_by(email="relation-freelancer@example.com").one()
            skill = Skill(name="Test relationship skill", category="Testing")
            freelancer.freelancer_profile.skills.append(FreelancerSkill(skill=skill))
            job = Job(client_id=client.id, title="Relationship test job", description="A sufficiently detailed description for this relationship test.", category="Testing")
            application = JobApplication(job=job, freelancer_id=freelancer.id,
                                         cover_letter="A sufficiently detailed cover letter for the relationship test.")
            db.session.add(application)
            db.session.commit()
            application_id, job_id = application.id, job.id
            self.assertIs(application.job, job)
            self.assertIs(application.freelancer, freelancer)
            self.assertIs(job.client, client)
            self.assertEqual(job.applications[0].id, application.id)
            self.assertIs(freelancer.freelancer_profile.skills[0].skill, skill)

            db.session.delete(job)
            db.session.commit()
            self.assertIsNone(db.session.get(JobApplication, application_id))
            self.assertIsNone(db.session.get(Job, job_id))

    def test_job_and_application_forms_reject_invalid_values(self):
        self.register(self.client_browser, "Validation Client", "validation-client@example.com", "client")
        profile_response = self.post_with_csrf(self.client_browser, "/profile", {
            "full_name": "Validation Client", "location": "Nairobi",
            "organization_name": "Validation Org", "website": "javascript:alert(1)",
            "bio": "A profile description for testing URL validation.",
        })
        self.assertEqual(profile_response.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(email="validation-client@example.com").one().client_profile.website)

        response = self.post_with_csrf(self.client_browser, "/jobs/new", {
            "title": "Bad", "category": "", "description": "Too short", "budget_min": "5000",
            "budget_max": "1000", "location": "Nairobi",
        })
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(Job.query.count(), 0)

        self.register(self.freelancer_browser, "Validation Student", "validation-student@example.com", "freelancer")
        response = self.post_with_csrf(self.client_browser, "/jobs/new", {
            "title": "Valid title for testing", "category": "Testing",
            "description": "A complete job description with enough useful information for applicants.",
            "budget_min": "5000", "budget_max": "9000", "location": "Nairobi",
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            job_id = Job.query.one().id
        response = self.post_with_csrf(self.freelancer_browser, f"/jobs/{job_id}/apply", {
            "cover_letter": "Too short", "proposed_rate": "-1",
        })
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(JobApplication.query.count(), 0)

    def test_job_and_freelancer_search_filters(self):
        self.register(self.client_browser, "Search Client", "search-client@example.com", "client")
        self.register(self.freelancer_browser, "Search Student", "search-freelancer@example.com", "freelancer")
        self.post_with_csrf(self.client_browser, "/jobs/new", {
            "title": "Nairobi Python dashboard", "category": "Development",
            "description": "Build a useful data dashboard with clear charts and responsive layouts.",
            "budget_min": "10000", "budget_max": "20000", "location": "Nairobi",
        })
        self.post_with_csrf(self.client_browser, "/jobs/new", {
            "title": "Mombasa brand illustration", "category": "Design",
            "description": "Create polished illustrations for a local business brand identity.",
            "budget_min": "30000", "budget_max": "40000", "location": "Mombasa",
        })
        response = self.post_with_csrf(self.freelancer_browser, "/profile", {
            "full_name": "Search Student", "location": "Nairobi", "headline": "Python developer",
            "bio": "I build dashboards and web applications for small teams.",
            "university": "Nairobi University", "hourly_rate": "1200", "skills": "Python, SQL",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        matches = self.client_browser.get("/jobs?q=dashboard&category=Development&location=Nairobi&budget_min=15000&budget_max=25000")
        self.assertEqual(matches.status_code, 200)
        self.assertIn(b"Nairobi Python dashboard", matches.data)
        self.assertNotIn(b"Mombasa brand illustration", matches.data)
        talent = self.client_browser.get("/freelancers?q=Python&skill=SQL&location=Nairobi")
        self.assertEqual(talent.status_code, 200)
        self.assertIn(b"Search Student", talent.data)
        no_match = self.client_browser.get("/freelancers?skill=NonexistentSkill")
        self.assertEqual(no_match.status_code, 200)
        self.assertNotIn(b"Search Student", no_match.data)

    def test_notifications_stay_unread_until_opened_and_are_private(self):
        self.register(self.client_browser, "Notification Owner", "notify@example.com", "client")
        self.register(self.outsider_browser, "Other Member", "other-notify@example.com", "client")
        with self.app.app_context():
            owner = User.query.filter_by(email="notify@example.com").one()
            notification = Notification(user_id=owner.id, kind="test", message="A private update.", target_url="/dashboard")
            db.session.add(notification)
            db.session.commit()
            notification_id = notification.id
        page = self.client_browser.get("/notifications")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"unread", page.data)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Notification, notification_id).read_at)
        self.assertEqual(self.outsider_browser.get(f"/notifications/{notification_id}/open").status_code, 404)
        opened = self.client_browser.get(f"/notifications/{notification_id}/open")
        self.assertEqual(opened.status_code, 302)
        self.assertEqual(opened.location, "/dashboard")
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Notification, notification_id).read_at)

    def test_invalid_search_filters_do_not_raise_server_errors(self):
        self.assertEqual(self.client_browser.get("/jobs?budget_min=not-money&budget_max=Infinity").status_code, 200)
        self.assertEqual(self.client_browser.get("/freelancers?rating=NaN").status_code, 200)

    def test_health_checks_database_and_security_headers_are_set(self):
        response = self.client_browser.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["database"], "connected")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("Content-Security-Policy", response.headers)

    def test_marketplace_schema_compiles_for_mysql(self):
        with self.app.app_context():
            for table in db.metadata.sorted_tables:
                self.assertTrue(str(CreateTable(table).compile(dialect=mysql.dialect())))

    def test_migrations_upgrade_downgrade_and_reupgrade(self):
        migration_dir = TemporaryDirectory()
        database_path = Path(migration_dir.name, "migration-check.db")
        migration_app = create_app(config_overrides=dict(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path.as_posix()}",
            SECRET_KEY="migration-test-secret",
            UPLOAD_FOLDER=migration_dir.name,
        ))
        runner = migration_app.test_cli_runner()
        try:
            result = runner.invoke(args=["db", "upgrade"])
            self.assertEqual(result.exit_code, 0, result.output)
            result = runner.invoke(args=["db", "downgrade", "base"])
            self.assertEqual(result.exit_code, 0, result.output)
            result = runner.invoke(args=["db", "upgrade"])
            self.assertEqual(result.exit_code, 0, result.output)
            with migration_app.app_context():
                self.assertEqual(set(db.metadata.tables), set(db.inspect(db.engine).get_table_names()) - {"alembic_version"})
        finally:
            with migration_app.app_context():
                db.session.remove()
                db.engine.dispose()
            migration_dir.cleanup()

    def test_database_rejects_invalid_project_amount(self):
        with self.app.app_context():
            client = User(email="constraint-client@example.com", full_name="Constraint Client", role="client")
            client.set_password("constraint-pass-2026")
            client.client_profile = ClientProfile()
            freelancer = User(email="constraint-freelancer@example.com", full_name="Constraint Freelancer", role="freelancer")
            freelancer.set_password("constraint-pass-2026")
            freelancer.freelancer_profile = FreelancerProfile()
            db.session.add_all([client, freelancer])
            db.session.flush()
            job = Job(client_id=client.id, title="Constraint job", description="A sufficiently long test job description.", category="Test", status="in_progress")
            db.session.add(job)
            db.session.flush()
            project = Project(job_id=job.id, client_id=client.id, freelancer_id=freelancer.id, agreed_amount=0)
            db.session.add(project)
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_csrf_rejects_post_without_token(self):
        self.app.config["WTF_CSRF_ENABLED"] = True
        response = self.client_browser.post("/auth/login", data={
            "email": "member@example.com",
            "password": "wrong-password",
        })
        self.assertEqual(response.status_code, 400)

    def test_rendered_logout_form_submits_its_csrf_token(self):
        self.app.config["WTF_CSRF_ENABLED"] = True
        response = self.client_browser.get("/auth/register")
        token_match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
        self.assertIsNotNone(token_match)
        token = token_match.group(1).decode()
        response = self.client_browser.post("/auth/register", data={
            "csrf_token": token,
            "full_name": "CSRF Member",
            "email": "csrf@example.com",
            "role": "client",
            "password": "secure-pass-2026",
            "confirm_password": "secure-pass-2026",
        })
        self.assertEqual(response.status_code, 302)
        home = self.client_browser.get("/")
        token_match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', home.data)
        self.assertIsNotNone(token_match)
        response = self.client_browser.post("/auth/logout", data={
            "csrf_token": token_match.group(1).decode(),
        })
        self.assertEqual(response.status_code, 302)

    def test_freelancer_can_track_and_withdraw_an_application(self):
        self.register(self.client_browser, "Hiring Client", "hiring@example.com", "client")
        self.register(self.freelancer_browser, "Applicant", "applicant@example.com", "freelancer")
        self.post_with_csrf(self.client_browser, "/jobs/new", {
            "title": "Write a student guide",
            "category": "Writing",
            "description": "Write and edit a practical guide for students preparing for internship applications.",
            "budget_min": "5000",
            "budget_max": "9000",
            "location": "Remote",
        })
        with self.app.app_context():
            job_id = db.session.query(Job).one().id
        self.post_with_csrf(self.freelancer_browser, f"/jobs/{job_id}/apply", {
            "cover_letter": "I have experience writing clear guides and can research the needs of university students.",
            "proposed_rate": "7500",
        })
        self.assertEqual(self.freelancer_browser.get("/my-applications").status_code, 200)
        response = self.post_with_csrf(self.freelancer_browser, "/applications/1/withdraw", {}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            from app.models import JobApplication
            self.assertEqual(db.session.query(JobApplication).one().status, "withdrawn")
            client_id = User.query.filter_by(email="hiring@example.com").one().id
            self.assertEqual(Notification.query.filter_by(user_id=client_id, kind="application_withdrawn").count(), 1)

    def test_client_profile_image_upload_does_not_skip_profile_fields(self):
        self.register(self.client_browser, "Client Profile", "profile-client@example.com", "client")
        response = self.post_with_csrf(self.client_browser, "/profile", {
            "full_name": "Client Profile",
            "location": "Nairobi",
            "organization_name": "First Organization",
            "website": "https://first.example.org",
            "bio": "We work with local students.",
            "profile_image": (BytesIO(b"\x89PNG\r\n\x1a\nfirst-image"), "first.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            profile = User.query.filter_by(email="profile-client@example.com").one().client_profile
            first_image = profile.profile_image
            self.assertEqual(profile.organization_name, "First Organization")
            self.assertTrue(Path(self.upload_dir.name, first_image).is_file())
        response = self.post_with_csrf(self.client_browser, "/profile", {
            "full_name": "Client Profile",
            "location": "Nairobi",
            "organization_name": "Updated Organization",
            "website": "https://updated.example.org",
            "bio": "Our updated description.",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            profile = User.query.filter_by(email="profile-client@example.com").one().client_profile
            self.assertEqual(profile.organization_name, "Updated Organization")
            self.assertEqual(profile.profile_image, first_image)
        response = self.post_with_csrf(self.client_browser, "/profile", {
            "full_name": "Client Profile",
            "location": "Nairobi",
            "organization_name": "Updated Organization",
            "website": "https://updated.example.org",
            "bio": "Our updated description.",
            "profile_image": (BytesIO(b"RIFF\x00\x00\x00\x00WEBPreplacement"), "replacement.webp"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            new_image = User.query.filter_by(email="profile-client@example.com").one().client_profile.profile_image
            self.assertTrue(Path(self.upload_dir.name, new_image).is_file())
            self.assertFalse(Path(self.upload_dir.name, first_image).exists())

    def test_admin_cli_and_suspension_invalidate_existing_session(self):
        result = self.app.test_cli_runner().invoke(
            args=["create-admin", "admin@example.com", "Site Admin"],
            input="SecureAdminPass99\nSecureAdminPass99\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.register(self.client_browser, "Member Account", "member@example.com", "client")
        self.post_with_csrf(self.freelancer_browser, "/auth/login", {
            "email": "admin@example.com",
            "password": "SecureAdminPass99",
        })
        self.assertEqual(self.freelancer_browser.get("/admin/users").status_code, 200)
        for path in ("/dashboard", "/admin/jobs", "/admin/applications", "/admin/reports"):
            self.assertEqual(self.freelancer_browser.get(path).status_code, 200, path)
        with self.app.app_context():
            member_id = User.query.filter_by(email="member@example.com").one().id
        response = self.post_with_csrf(self.freelancer_browser, f"/admin/users/{member_id}/toggle", {}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        response = self.client_browser.get("/dashboard", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Log in to SkillLink", response.data)

    def test_report_resolution_and_admin_job_moderation(self):
        self.register(self.client_browser, "Reporting Client", "reporter@example.com", "client")
        self.register(self.freelancer_browser, "Reported Freelancer", "reported@example.com", "freelancer")
        result = self.app.test_cli_runner().invoke(
            args=["create-admin", "moderator@example.com", "Moderator"],
            input="SecureModeratorPass99\nSecureModeratorPass99\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            target_id = User.query.filter_by(email="reported@example.com").one().id
        response = self.post_with_csrf(self.client_browser, f"/reports/{target_id}/new", {
            "reason": "spam",
            "details": "This profile is sending unsolicited offers.",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.post_with_csrf(self.client_browser, "/jobs/new", {
            "title": "Create an event poster",
            "category": "Design",
            "description": "Design a clear promotional poster for a student technology event happening next month.",
            "budget_min": "3000",
            "budget_max": "5000",
            "location": "Nairobi",
        })
        self.post_with_csrf(self.freelancer_browser, "/auth/logout", {})
        self.post_with_csrf(self.freelancer_browser, "/auth/login", {
            "email": "moderator@example.com",
            "password": "SecureModeratorPass99",
        })
        with self.app.app_context():
            report_id = db.session.query(Report).one().id
            job_id = db.session.query(Job).one().id
            moderator_id = User.query.filter_by(email="moderator@example.com").one().id
            self.assertEqual(Notification.query.filter_by(user_id=moderator_id, kind="member_report").count(), 1)
        self.assertEqual(self.freelancer_browser.get("/admin/reports").status_code, 200)
        self.assertEqual(self.freelancer_browser.get("/admin/jobs").status_code, 200)
        self.post_with_csrf(self.freelancer_browser, f"/admin/reports/{report_id}/resolve", {}, follow_redirects=True)
        self.post_with_csrf(self.freelancer_browser, f"/admin/jobs/{job_id}/toggle", {}, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(db.session.get(Report, report_id).status, "resolved")
            self.assertEqual(db.session.get(Job, job_id).status, "cancelled")


if __name__ == "__main__":
    unittest.main()
