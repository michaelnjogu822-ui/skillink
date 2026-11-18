"""Add relational integrity checks for marketplace records.

Revision ID: 9c37d431c2e1
Revises: 527e1259af25
Create Date: 2026-11-18
"""
from alembic import op


revision = "9c37d431c2e1"
down_revision = "527e1259af25"
branch_labels = None
depends_on = None


CONSTRAINTS = {
    "freelancer_profiles": [
        ("ck_freelancer_hourly_rate_nonnegative", "hourly_rate IS NULL OR hourly_rate >= 0"),
        ("ck_freelancer_rating_range", "average_rating BETWEEN 0 AND 5"),
        ("ck_freelancer_review_count_nonnegative", "review_count >= 0"),
    ],
    "freelancer_skills": [
        ("ck_freelancer_skill_proficiency", "proficiency IN ('beginner', 'intermediate', 'advanced', 'expert')"),
        ("ck_freelancer_skill_experience_nonnegative", "years_experience IS NULL OR years_experience >= 0"),
    ],
    "jobs": [
        ("ck_jobs_budget_min_nonnegative", "budget_min IS NULL OR budget_min >= 0"),
        ("ck_jobs_budget_max_nonnegative", "budget_max IS NULL OR budget_max >= 0"),
    ],
    "job_applications": [
        ("ck_applications_rate_positive", "proposed_rate IS NULL OR proposed_rate > 0"),
    ],
    "projects": [
        ("ck_projects_amount_positive", "agreed_amount > 0"),
        ("ck_projects_distinct_members", "client_id != freelancer_id"),
    ],
    "reviews": [
        ("ck_reviews_distinct_members", "reviewer_id != reviewee_id"),
    ],
    "reports": [
        ("ck_reports_distinct_members", "reporter_id != reported_user_id"),
    ],
    "transactions": [
        ("ck_transactions_distinct_members", "payer_id != payee_id"),
    ],
}


def upgrade():
    for table_name, constraints in CONSTRAINTS.items():
        with op.batch_alter_table(table_name) as batch_op:
            for name, condition in constraints:
                batch_op.create_check_constraint(name, condition)


def downgrade():
    for table_name, constraints in reversed(list(CONSTRAINTS.items())):
        with op.batch_alter_table(table_name) as batch_op:
            for name, _condition in reversed(constraints):
                batch_op.drop_constraint(name, type_="check")
