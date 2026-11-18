"""Database-backed discovery and marketplace workflows."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
import math

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.forms import ApplicationForm, JobForm, MessageForm, ReviewForm
from app.models import (FreelancerProfile, FreelancerSkill, Job, JobApplication,
                        Message, Notification, Project, Review, Skill, User)
from app.services import escrow
from app.services.escrow import PaymentError

marketplace_bp = Blueprint("marketplace", __name__)


def locked_project(project_id):
    """Load a project with a row lock so two payments cannot race each other."""
    return db.session.execute(
        db.select(Project).where(Project.id == project_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def budget_filter(name):
    raw_value = request.args.get(name, "").strip()
    if not raw_value:
        return None
    try:
        value = Decimal(raw_value)
    except InvalidOperation:
        flash("Budget filters must be valid non-negative amounts.", "warning")
        return None
    if not value.is_finite() or value < 0:
        flash("Budget filters must be valid non-negative amounts.", "warning")
        return None
    return value


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@marketplace_bp.get("/freelancers")
def freelancers():
    query = User.query.join(FreelancerProfile).options(
        selectinload(User.freelancer_profile)
        .selectinload(FreelancerProfile.skills)
        .joinedload(FreelancerSkill.skill)
    ).filter(User.is_active.is_(True))
    term = request.args.get("q", "").strip()[:120]
    location = request.args.get("location", "").strip()[:120]
    skill = request.args.get("skill", "").strip()[:80]
    minimum_rating = request.args.get("rating", type=float)
    if minimum_rating is not None and (not math.isfinite(minimum_rating) or not 0 <= minimum_rating <= 5):
        minimum_rating = None
    if term:
        query = query.filter(or_(User.full_name.ilike(f"%{term}%"), FreelancerProfile.headline.ilike(f"%{term}%"), FreelancerProfile.bio.ilike(f"%{term}%")))
    if location:
        query = query.filter(User.location.ilike(f"%{location}%"))
    if skill:
        query = query.join(FreelancerSkill).join(Skill).filter(Skill.name.ilike(f"%{skill}%"))
    if minimum_rating is not None:
        query = query.filter(FreelancerProfile.average_rating >= minimum_rating)
    people = query.order_by(FreelancerProfile.average_rating.desc(), User.created_at.desc()).limit(60).all()
    return render_template("marketplace/freelancers.html", freelancers=people)


@marketplace_bp.get("/freelancers/<int:user_id>")
def freelancer_detail(user_id):
    person = User.query.options(
        selectinload(User.freelancer_profile).selectinload(FreelancerProfile.skills).joinedload(FreelancerSkill.skill),
        selectinload(User.freelancer_profile).selectinload(FreelancerProfile.portfolios),
        selectinload(User.reviews_received).joinedload(Review.reviewer),
    ).filter_by(id=user_id, role="freelancer", is_active=True).first_or_404()
    return render_template("marketplace/freelancer_detail.html", person=person)


@marketplace_bp.get("/jobs")
def jobs():
    query = Job.query.options(joinedload(Job.client)).join(
        User, Job.client_id == User.id
    ).filter(Job.status == "open", User.is_active.is_(True))
    term = request.args.get("q", "").strip()[:160]
    category = request.args.get("category", "").strip()[:80]
    location = request.args.get("location", "").strip()[:120]
    budget_min = budget_filter("budget_min")
    budget_max = budget_filter("budget_max")
    if budget_min is not None and budget_max is not None and budget_min > budget_max:
        flash("Minimum budget cannot be greater than maximum budget.", "warning")
        budget_min = budget_max = None
    if term:
        query = query.filter(or_(Job.title.ilike(f"%{term}%"), Job.description.ilike(f"%{term}%")))
    if category:
        query = query.filter(Job.category.ilike(f"%{category}%"))
    if location:
        query = query.filter(Job.location.ilike(f"%{location}%"))
    if budget_min is not None:
        query = query.filter(or_(Job.budget_max.is_(None), Job.budget_max >= budget_min))
    if budget_max is not None:
        query = query.filter(or_(Job.budget_min.is_(None), Job.budget_min <= budget_max))
    results = query.order_by(Job.created_at.desc()).limit(80).all()
    return render_template("marketplace/jobs.html", jobs=results)


@marketplace_bp.route("/jobs/new", methods=["GET", "POST"])
@role_required("client")
def job_new():
    form = JobForm()
    if form.validate_on_submit():
        job = Job(client_id=current_user.id, title=form.title.data.strip(), category=form.category.data.strip(), description=form.description.data.strip(), budget_min=form.budget_min.data, budget_max=form.budget_max.data, location=form.location.data.strip() or None)
        db.session.add(job)
        db.session.commit()
        flash("Your job is live and ready for applications.", "success")
        return redirect(url_for("marketplace.job_detail", job_id=job.id))
    return render_template("marketplace/job_form.html", form=form)


@marketplace_bp.post("/jobs/<int:job_id>/toggle")
@role_required("client")
def toggle_client_job(job_id):
    job = db.get_or_404(Job, job_id)
    if job.client_id != current_user.id:
        abort(403)
    if job.status == "open":
        job.status = "cancelled"
    elif job.status == "cancelled":
        job.status = "open"
    else:
        abort(409)
    db.session.commit()
    flash("Job listing updated.", "success")
    return redirect(url_for("dashboard.index"))


@marketplace_bp.get("/jobs/<int:job_id>")
def job_detail(job_id):
    job = Job.query.options(joinedload(Job.client)).filter_by(id=job_id).first_or_404()
    if job.status != "open":
        allowed = current_user.is_authenticated and (
            current_user.role == "admin" or job.client_id == current_user.id or
            JobApplication.query.filter_by(job_id=job.id, freelancer_id=current_user.id).first() is not None or
            Project.query.filter_by(job_id=job.id, freelancer_id=current_user.id).first() is not None
        )
        if not allowed:
            abort(404)
    form = ApplicationForm()
    already_applied = current_user.is_authenticated and JobApplication.query.filter_by(job_id=job.id, freelancer_id=current_user.id).first() is not None
    return render_template("marketplace/job_detail.html", job=job, form=form, already_applied=already_applied)


@marketplace_bp.post("/jobs/<int:job_id>/apply")
@role_required("freelancer")
def apply_job(job_id):
    job = db.get_or_404(Job, job_id)
    form = ApplicationForm()
    if job.status != "open":
        abort(409)
    if JobApplication.query.filter_by(job_id=job.id, freelancer_id=current_user.id).first():
        flash("You have already applied to this job.", "info")
        return redirect(url_for("marketplace.job_detail", job_id=job.id))
    if form.validate_on_submit():
        application = JobApplication(job_id=job.id, freelancer_id=current_user.id, cover_letter=form.cover_letter.data.strip(), proposed_rate=form.proposed_rate.data)
        db.session.add(application)
        db.session.add(Notification(user_id=job.client_id, kind="job_application", message=f"{current_user.full_name} applied for {job.title}.", target_url=url_for("dashboard.applications")))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("You have already applied to this job.", "info")
            return redirect(url_for("marketplace.job_detail", job_id=job.id))
        flash("Application sent successfully.", "success")
        return redirect(url_for("dashboard.index"))
    return render_template("marketplace/job_detail.html", job=job, form=form, already_applied=False)


@marketplace_bp.post("/projects/<int:project_id>/submit")
@role_required("freelancer")
def submit_project(project_id):
    project = db.session.execute(
        db.select(Project).where(Project.id == project_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if project is None:
        abort(404)
    if project.freelancer_id != current_user.id:
        abort(403)
    if project.status != "active":
        abort(409)
    project.status = "submitted"
    project.submitted_at = datetime.now(timezone.utc)
    db.session.add(Notification(user_id=project.client_id, kind="project_submitted", message=f"Work was submitted for {project.job.title}.", target_url=url_for("marketplace.project_detail", project_id=project.id)))
    db.session.commit()
    flash("Work submitted for client review.", "success")
    return redirect(url_for("marketplace.project_detail", project_id=project.id))


@marketplace_bp.post("/applications/<int:application_id>/hire")
@role_required("client")
def hire_application(application_id):
    application = db.get_or_404(JobApplication, application_id)
    job = db.session.execute(
        db.select(Job).where(Job.id == application.job_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if job is None:
        abort(404)
    application = db.session.execute(
        db.select(JobApplication).where(JobApplication.id == application_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if job.client_id != current_user.id:
        abort(403)
    if application.status not in ("pending", "shortlisted") or job.status != "open":
        abort(409)
    amount = application.proposed_rate or job.budget_max or job.budget_min
    if amount is None or amount <= 0:
        flash("Set a proposed rate or add a job budget before hiring.", "warning")
        return redirect(url_for("dashboard.applications"))
    application.status = "accepted"
    job.status = "in_progress"
    for other in job.applications:
        if other.id != application.id and other.status in ("pending", "shortlisted"):
            other.status = "rejected"
    project = Project(job_id=job.id, client_id=current_user.id, freelancer_id=application.freelancer_id, agreed_amount=amount)
    db.session.add(project)
    db.session.add(Notification(user_id=application.freelancer_id, kind="hired", message=f"You were hired for {job.title}.", target_url=url_for("dashboard.projects")))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This job was just hired for. Refresh the application list to see its latest status.", "warning")
        return redirect(url_for("dashboard.applications"))
    flash("Freelancer hired. Your project is ready.", "success")
    return redirect(url_for("marketplace.project_detail", project_id=project.id))


@marketplace_bp.get("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = db.get_or_404(Project, project_id)
    if current_user.id not in (project.client_id, project.freelancer_id) and current_user.role != "admin":
        abort(403)
    form = MessageForm()
    review_form = ReviewForm()
    review_exists = Review.query.filter_by(project_id=project.id, reviewer_id=current_user.id).first() is not None
    unread_messages = Message.query.filter(
        Message.project_id == project.id,
        Message.sender_id != current_user.id,
        Message.read_at.is_(None),
    ).all() if current_user.id in (project.client_id, project.freelancer_id) else []
    if unread_messages:
        now = datetime.now(timezone.utc)
        for message in unread_messages:
            message.read_at = now
        db.session.commit()
    counterpart_id = project.freelancer_id if current_user.id == project.client_id else project.client_id
    counterpart_review = Review.query.filter_by(project_id=project.id, reviewer_id=counterpart_id).first()
    escrow_record = escrow.escrow_transaction(project)
    return render_template("marketplace/project.html", project=project, form=form, review_form=review_form,
                           review_exists=review_exists, counterpart_review=counterpart_review,
                           escrow_record=escrow_record, escrow_state=escrow.escrow_state(project),
                           escrow_labels=escrow.STATE_LABELS)


@marketplace_bp.post("/projects/<int:project_id>/messages")
@login_required
def send_message(project_id):
    project = db.get_or_404(Project, project_id)
    if current_user.id not in (project.client_id, project.freelancer_id):
        abort(403)
    form = MessageForm()
    if form.validate_on_submit():
        message = Message(project_id=project.id, sender_id=current_user.id, body=form.body.data.strip())
        db.session.add(message)
        recipient_id = project.freelancer_id if current_user.id == project.client_id else project.client_id
        db.session.add(Notification(user_id=recipient_id, kind="message", message=f"New message about {project.job.title}.", target_url=url_for("marketplace.project_detail", project_id=project.id)))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Your message could not be saved. Please try again.", "danger")
            return redirect(url_for("marketplace.project_detail", project_id=project.id))
    else:
        flash("Please enter a message.", "danger")
    return redirect(url_for("marketplace.project_detail", project_id=project.id) + "#messages")


@marketplace_bp.post("/projects/<int:project_id>/fund")
@role_required("client")
def fund_project_escrow(project_id):
    project = locked_project(project_id)
    if project is None:
        abort(404)
    if project.client_id != current_user.id:
        abort(403)
    try:
        escrow.fund_project(project, current_user)
        db.session.commit()
    except PaymentError as error:
        db.session.rollback()
        flash(str(error), "warning")
        return redirect(url_for("marketplace.project_detail", project_id=project.id))
    flash("Payment received. The money is held in escrow until you approve the work.", "success")
    return redirect(url_for("marketplace.project_detail", project_id=project.id))


@marketplace_bp.post("/projects/<int:project_id>/cancel")
@role_required("client")
def cancel_project(project_id):
    project = locked_project(project_id)
    if project is None:
        abort(404)
    if project.client_id != current_user.id:
        abort(403)
    if project.status not in ("active", "submitted"):
        abort(409)
    refunded = False
    try:
        if escrow.escrow_state(project) == "held":
            escrow.refund_escrow(project)
            refunded = True
    except PaymentError as error:
        db.session.rollback()
        flash(str(error), "danger")
        return redirect(url_for("marketplace.project_detail", project_id=project.id))
    project.status = "cancelled"
    project.job.status = "cancelled"
    db.session.add(Notification(
        user_id=project.freelancer_id,
        kind="project_cancelled",
        message=f"{project.job.title} was cancelled by the client.",
        target_url=url_for("dashboard.projects"),
    ))
    db.session.commit()
    message = "Project cancelled."
    if refunded:
        message += " The escrow amount was refunded to you."
    flash(message, "info")
    return redirect(url_for("marketplace.project_detail", project_id=project.id))


@marketplace_bp.post("/projects/<int:project_id>/complete")
@role_required("client")
def complete_project(project_id):
    project = locked_project(project_id)
    if project is None:
        abort(404)
    if project.client_id != current_user.id:
        abort(403)
    if project.status != "submitted":
        abort(409)
    if escrow.escrow_state(project) != "held":
        flash("Fund the escrow before you approve the work.", "warning")
        return redirect(url_for("marketplace.project_detail", project_id=project.id))
    try:
        escrow.release_escrow(project)
    except PaymentError as error:
        db.session.rollback()
        flash(str(error), "danger")
        return redirect(url_for("marketplace.project_detail", project_id=project.id))
    project.status = "completed"
    project.completed_at = datetime.now(timezone.utc)
    project.job.status = "completed"
    db.session.add(Notification(user_id=project.freelancer_id, kind="project_completed", message=f"{project.job.title} was marked complete.", target_url=url_for("marketplace.project_detail", project_id=project.id)))
    db.session.commit()
    flash("Project marked complete. The escrow payment was released to the freelancer.", "success")
    return redirect(url_for("marketplace.project_detail", project_id=project.id))


@marketplace_bp.post("/projects/<int:project_id>/review")
@login_required
def review_project(project_id):
    project = db.get_or_404(Project, project_id)
    if current_user.id not in (project.client_id, project.freelancer_id):
        abort(403)
    if project.status != "completed":
        abort(409)
    if Review.query.filter_by(project_id=project.id, reviewer_id=current_user.id).first():
        flash("You have already reviewed this project.", "info")
        return redirect(url_for("marketplace.project_detail", project_id=project.id))
    form = ReviewForm()
    if form.validate_on_submit():
        reviewee_id = project.freelancer_id if current_user.id == project.client_id else project.client_id
        review = Review(project_id=project.id, reviewer_id=current_user.id, reviewee_id=reviewee_id, rating=form.rating.data, comment=form.comment.data.strip() if form.comment.data else None)
        db.session.add(review)
        if reviewee_id == project.freelancer_id:
            profile = db.session.execute(
                db.select(FreelancerProfile)
                .where(FreelancerProfile.user_id == reviewee_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
            count = profile.review_count
            profile.average_rating = ((Decimal(profile.average_rating) * count + Decimal(review.rating)) / Decimal(count + 1)).quantize(Decimal("0.01"))
            profile.review_count = count + 1
        review_target = (
            url_for("marketplace.freelancer_detail", user_id=reviewee_id)
            if reviewee_id == project.freelancer_id
            else url_for("dashboard.profile")
        )
        db.session.add(Notification(
            user_id=reviewee_id,
            kind="review_received",
            message=f"{current_user.full_name} left you a {review.rating}-star review.",
            target_url=review_target,
        ))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A review has already been submitted for this project.", "info")
            return redirect(url_for("marketplace.project_detail", project_id=project.id))
        flash("Thank you for sharing your feedback.", "success")
    return redirect(url_for("marketplace.project_detail", project_id=project.id))
