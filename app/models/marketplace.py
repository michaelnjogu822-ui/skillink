"""Marketplace entities and their relational constraints."""
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager


def utcnow():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    location = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    last_seen_at = db.Column(db.DateTime(timezone=True))

    freelancer_profile = db.relationship("FreelancerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    client_profile = db.relationship("ClientProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    reviews_received = db.relationship("Review", foreign_keys="Review.reviewee_id", back_populates="reviewee")

    __table_args__ = (db.CheckConstraint("role IN ('freelancer', 'client', 'admin')", name="ck_users_role"),)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    try:
        user = db.session.get(User, int(user_id))
        return user if user and user.is_active else None
    except (TypeError, ValueError):
        return None


class FreelancerProfile(TimestampMixin, db.Model):
    __tablename__ = "freelancer_profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    headline = db.Column(db.String(160))
    bio = db.Column(db.Text)
    hourly_rate = db.Column(db.Numeric(10, 2))
    currency = db.Column(db.String(3), nullable=False, default="KES")
    profile_image = db.Column(db.String(255))
    university = db.Column(db.String(160))
    graduation_year = db.Column(db.SmallInteger)
    availability = db.Column(db.String(20), nullable=False, default="available")
    average_rating = db.Column(db.Numeric(3, 2), nullable=False, default=0)
    review_count = db.Column(db.Integer, nullable=False, default=0)

    user = db.relationship("User", back_populates="freelancer_profile")
    skills = db.relationship("FreelancerSkill", back_populates="freelancer", cascade="all, delete-orphan")
    portfolios = db.relationship("Portfolio", back_populates="freelancer", cascade="all, delete-orphan")
    __table_args__ = (
        db.CheckConstraint("hourly_rate IS NULL OR hourly_rate >= 0", name="ck_freelancer_hourly_rate_nonnegative"),
        db.CheckConstraint("average_rating BETWEEN 0 AND 5", name="ck_freelancer_rating_range"),
        db.CheckConstraint("review_count >= 0", name="ck_freelancer_review_count_nonnegative"),
    )


class ClientProfile(TimestampMixin, db.Model):
    __tablename__ = "client_profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    organization_name = db.Column(db.String(160))
    website = db.Column(db.String(255))
    description = db.Column(db.Text)
    profile_image = db.Column(db.String(255))

    user = db.relationship("User", back_populates="client_profile")


class Skill(TimestampMixin, db.Model):
    __tablename__ = "skills"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    category = db.Column(db.String(80), nullable=False, index=True)
    freelancer_links = db.relationship("FreelancerSkill", back_populates="skill")


class FreelancerSkill(db.Model):
    __tablename__ = "freelancer_skills"
    freelancer_id = db.Column(db.Integer, db.ForeignKey("freelancer_profiles.id", ondelete="CASCADE"), primary_key=True)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True)
    proficiency = db.Column(db.String(20), nullable=False, default="intermediate")
    years_experience = db.Column(db.Numeric(4, 1))
    freelancer = db.relationship("FreelancerProfile", back_populates="skills")
    skill = db.relationship("Skill", back_populates="freelancer_links")
    __table_args__ = (
        db.CheckConstraint("proficiency IN ('beginner', 'intermediate', 'advanced', 'expert')", name="ck_freelancer_skill_proficiency"),
        db.CheckConstraint("years_experience IS NULL OR years_experience >= 0", name="ck_freelancer_skill_experience_nonnegative"),
    )


class Portfolio(TimestampMixin, db.Model):
    __tablename__ = "portfolios"
    id = db.Column(db.Integer, primary_key=True)
    freelancer_id = db.Column(db.Integer, db.ForeignKey("freelancer_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    project_url = db.Column(db.String(500))
    image_path = db.Column(db.String(255))
    freelancer = db.relationship("FreelancerProfile", back_populates="portfolios")


class Job(TimestampMixin, db.Model):
    __tablename__ = "jobs"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80), nullable=False, index=True)
    budget_min = db.Column(db.Numeric(12, 2))
    budget_max = db.Column(db.Numeric(12, 2))
    currency = db.Column(db.String(3), nullable=False, default="KES")
    location = db.Column(db.String(120), index=True)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    deadline = db.Column(db.Date)
    client = db.relationship("User", foreign_keys=[client_id])
    applications = db.relationship("JobApplication", back_populates="job", cascade="all, delete-orphan")
    __table_args__ = (
        db.CheckConstraint("status IN ('draft', 'open', 'in_progress', 'completed', 'cancelled')", name="ck_jobs_status"),
        db.CheckConstraint("budget_min IS NULL OR budget_min >= 0", name="ck_jobs_budget_min_nonnegative"),
        db.CheckConstraint("budget_max IS NULL OR budget_max >= 0", name="ck_jobs_budget_max_nonnegative"),
        db.CheckConstraint("budget_min IS NULL OR budget_max IS NULL OR budget_min <= budget_max", name="ck_jobs_budget_range"),
    )


class JobApplication(TimestampMixin, db.Model):
    __tablename__ = "job_applications"
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    freelancer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    cover_letter = db.Column(db.Text, nullable=False)
    proposed_rate = db.Column(db.Numeric(12, 2))
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    job = db.relationship("Job", back_populates="applications")
    freelancer = db.relationship("User", foreign_keys=[freelancer_id])
    __table_args__ = (
        db.UniqueConstraint("job_id", "freelancer_id", name="uq_application_job_freelancer"),
        db.CheckConstraint("status IN ('pending', 'shortlisted', 'accepted', 'rejected', 'withdrawn')", name="ck_applications_status"),
        db.CheckConstraint("proposed_rate IS NULL OR proposed_rate > 0", name="ck_applications_rate_positive"),
    )


class Project(TimestampMixin, db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    freelancer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    agreed_amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="KES")
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    submitted_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    job = db.relationship("Job")
    client = db.relationship("User", foreign_keys=[client_id])
    freelancer = db.relationship("User", foreign_keys=[freelancer_id])
    messages = db.relationship("Message", back_populates="project", cascade="all, delete-orphan")
    transactions = db.relationship("Transaction", back_populates="project", order_by="Transaction.id")
    __table_args__ = (
        db.CheckConstraint("status IN ('active', 'submitted', 'completed', 'cancelled', 'disputed')", name="ck_projects_status"),
        db.CheckConstraint("agreed_amount > 0", name="ck_projects_amount_positive"),
        db.CheckConstraint("client_id != freelancer_id", name="ck_projects_distinct_members"),
    )


class Message(TimestampMixin, db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    read_at = db.Column(db.DateTime(timezone=True))
    project = db.relationship("Project", back_populates="messages")
    sender = db.relationship("User")


class Notification(TimestampMixin, db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(60), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    target_url = db.Column(db.String(500))
    read_at = db.Column(db.DateTime(timezone=True))
    user = db.relationship("User")


class NotificationPreference(db.Model):
    """Per-user switch for a category of in-app notification.

    A missing row means the category is enabled, so a new account receives
    notifications without any setup.
    """

    __tablename__ = "notification_preferences"
    CATEGORIES = ("applications", "projects", "messages", "payments", "reviews")
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    category = db.Column(db.String(20), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    user = db.relationship("User")
    __table_args__ = (
        db.CheckConstraint(
            "category IN ('applications', 'projects', 'messages', 'payments', 'reviews')",
            name="ck_notification_preferences_category",
        ),
    )


class Review(TimestampMixin, db.Model):
    __tablename__ = "reviews"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewee_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    rating = db.Column(db.SmallInteger, nullable=False)
    comment = db.Column(db.Text)
    project = db.relationship("Project")
    reviewer = db.relationship("User", foreign_keys=[reviewer_id])
    reviewee = db.relationship("User", foreign_keys=[reviewee_id], back_populates="reviews_received")
    __table_args__ = (
        db.UniqueConstraint("project_id", "reviewer_id", name="uq_review_project_reviewer"),
        db.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating"),
        db.CheckConstraint("reviewer_id != reviewee_id", name="ck_reviews_distinct_members"),
    )


class Transaction(TimestampMixin, db.Model):
    """A payment record.

    Escrow payments follow a lifecycle: ``held`` while the client's money is
    held by the platform, then ``released`` to the freelancer when the client
    approves the work, or ``refunded`` to the client when the project is
    cancelled. Rows with kind ``manual`` are direct payments recorded outside
    the escrow flow (for example imported history) and use ``succeeded``.
    """

    __tablename__ = "transactions"
    KIND_MANUAL = "manual"
    KIND_ESCROW = "escrow"
    ESCROW_STATUSES = ("held", "released", "refunded")
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    payer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    payee_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="KES")
    kind = db.Column(db.String(20), nullable=False, default=KIND_MANUAL, server_default=KIND_MANUAL, index=True)
    provider = db.Column(db.String(40), nullable=False, default="unconfigured")
    provider_reference = db.Column(db.String(160), unique=True)
    settlement_reference = db.Column(db.String(160), unique=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    project = db.relationship("Project", back_populates="transactions")
    payer = db.relationship("User", foreign_keys=[payer_id])
    payee = db.relationship("User", foreign_keys=[payee_id])
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_transactions_positive_amount"),
        db.CheckConstraint(
            "status IN ('pending', 'held', 'released', 'succeeded', 'failed', 'refunded')",
            name="ck_transactions_status",
        ),
        db.CheckConstraint("payer_id != payee_id", name="ck_transactions_distinct_members"),
        db.CheckConstraint("kind IN ('manual', 'escrow')", name="ck_transactions_kind"),
    )


class Report(TimestampMixin, db.Model):
    __tablename__ = "reports"
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    reported_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    reason = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at = db.Column(db.DateTime(timezone=True))
    reporter = db.relationship("User", foreign_keys=[reporter_id])
    reported_user = db.relationship("User", foreign_keys=[reported_user_id])
    moderator = db.relationship("User", foreign_keys=[resolved_by])
    __table_args__ = (
        db.CheckConstraint("status IN ('open', 'reviewing', 'resolved', 'dismissed')", name="ck_reports_status"),
        db.CheckConstraint("reporter_id != reported_user_id", name="ck_reports_distinct_members"),
    )
