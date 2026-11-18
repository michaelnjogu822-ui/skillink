"""Role-specific account dashboards and moderation actions."""
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.utils import secure_filename

from app.extensions import db
from app.forms import PortfolioForm, ProfileForm, ReportForm
from app.services import notifications as notifications_service
from app.models import (FreelancerSkill, Job, JobApplication, Message, Notification,
                        Portfolio, Project, Report, Skill, Transaction, User)

dashboard_bp = Blueprint("dashboard", __name__)


def store_image(upload):
    extension = Path(upload.filename).suffix.lower()
    stored_name = secure_filename(f"{uuid4().hex}{extension}")
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    upload_folder.mkdir(parents=True, exist_ok=True)
    upload.save(upload_folder / stored_name)
    return stored_name


def remove_uploaded_file(filename):
    if not filename:
        return
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    candidate = (upload_folder / filename).resolve()
    if candidate.parent == upload_folder and candidate.is_file():
        candidate.unlink()


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@dashboard_bp.get("/dashboard")
@login_required
def index():
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(6).all()
    if current_user.role == "freelancer":
        applications = JobApplication.query.filter_by(freelancer_id=current_user.id).count()
        projects = Project.query.filter_by(freelancer_id=current_user.id)
        active = projects.filter(Project.status.in_(["active", "submitted"])).count()
        completed = projects.filter_by(status="completed").count()
        recent_projects = projects.options(joinedload(Project.job)).order_by(Project.updated_at.desc()).limit(5).all()
        profile = current_user.freelancer_profile
        skill_names = [name for (name,) in db.session.query(Skill.name).join(
            FreelancerSkill, FreelancerSkill.skill_id == Skill.id
        ).filter(FreelancerSkill.freelancer_id == profile.id).all()]
        open_jobs = Job.query.join(User, Job.client_id == User.id).filter(
            Job.status == "open", User.is_active.is_(True)
        )
        recent_jobs = []
        if skill_names:
            matches = [
                or_(Job.title.ilike(f"%{name}%"), Job.description.ilike(f"%{name}%"), Job.category.ilike(f"%{name}%"))
                for name in skill_names[:20]
            ]
            recent_jobs = open_jobs.filter(or_(*matches)).order_by(Job.created_at.desc()).limit(5).all()
        if len(recent_jobs) < 5:
            matched_ids = [job.id for job in recent_jobs]
            fallback = open_jobs.filter(~Job.id.in_(matched_ids)) if matched_ids else open_jobs
            recent_jobs.extend(fallback.order_by(Job.created_at.desc()).limit(5 - len(recent_jobs)).all())
        fields = [current_user.location, profile.headline, profile.bio, profile.university, profile.hourly_rate]
        completion = int(sum(value not in (None, "") for value in fields) * 100 / len(fields))
        earned = db.session.query(Transaction.currency, db.func.sum(Transaction.amount)).filter(
            Transaction.payee_id == current_user.id,
            Transaction.status.in_(("succeeded", "released")),
        ).group_by(Transaction.currency).all()
        earned_label = ", ".join(f"{currency} {amount:,.2f}" for currency, amount in earned) or "No payments yet"
        stats = [("Applications", applications), ("Active projects", active), ("Completed", completed), ("Earnings", earned_label)]
        return render_template("dashboard/index.html", stats=stats, notifications=notifications, projects=recent_projects, jobs=recent_jobs, completion=completion)
    if current_user.role == "client":
        jobs = Job.query.options(selectinload(Job.applications)).filter_by(client_id=current_user.id)
        applications = JobApplication.query.join(Job).filter(Job.client_id == current_user.id).count()
        active = Project.query.filter_by(client_id=current_user.id).filter(Project.status.in_(["active", "submitted"])).count()
        hired = Project.query.filter_by(client_id=current_user.id).count()
        stats = [("Jobs posted", jobs.count()), ("Applications", applications), ("Freelancers hired", hired), ("Active projects", active)]
        recent_jobs = jobs.order_by(Job.created_at.desc()).limit(5).all()
        recent_projects = Project.query.options(joinedload(Project.job)).filter_by(client_id=current_user.id).order_by(Project.updated_at.desc()).limit(5).all()
        return render_template("dashboard/index.html", stats=stats, notifications=notifications, jobs=recent_jobs, projects=recent_projects)
    stats = [("Registered users", User.query.count()), ("Freelancers", User.query.filter_by(role="freelancer").count()), ("Clients", User.query.filter_by(role="client").count()), ("Jobs", Job.query.count()), ("Active projects", Project.query.filter(Project.status.in_(["active", "submitted"])).count()), ("Open reports", Report.query.filter_by(status="open").count())]
    return render_template("dashboard/index.html", stats=stats, notifications=notifications, reports=Report.query.filter_by(status="open").order_by(Report.created_at.desc()).limit(5).all())


@dashboard_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm()
    if current_user.role == "freelancer":
        profile = current_user.freelancer_profile
        if not form.is_submitted():
            form.headline.data = profile.headline
            form.bio.data = profile.bio
            form.university.data = profile.university
            form.hourly_rate.data = profile.hourly_rate
            form.skills.data = ", ".join(link.skill.name for link in profile.skills)
    elif current_user.role == "client":
        profile = current_user.client_profile
        if not form.is_submitted():
            form.organization_name.data = profile.organization_name
            form.website.data = profile.website
            form.bio.data = profile.description
    else:
        profile = None
    if form.validate_on_submit():
        current_user.full_name = form.full_name.data.strip()
        current_user.location = form.location.data.strip() or None
        if current_user.role == "freelancer":
            profile.headline = form.headline.data.strip() or None
            profile.bio = form.bio.data.strip() or None
            profile.university = form.university.data.strip() or None
            profile.hourly_rate = form.hourly_rate.data
            requested_skills = list(dict.fromkeys(name.strip() for name in (form.skills.data or "").split(",") if name.strip()))[:20]
            profile.skills.clear()
            for name in requested_skills:
                skill = Skill.query.filter(db.func.lower(Skill.name) == name.lower()).first()
                if skill is None:
                    skill = Skill(name=name[:80], category="General")
                    db.session.add(skill)
                    db.session.flush()
                profile.skills.append(FreelancerSkill(skill=skill))
        upload = form.profile_image.data
        old_image = profile.profile_image if profile is not None else None
        new_image = None
        if current_user.role in ("freelancer", "client") and upload and upload.filename:
            new_image = store_image(upload)
            profile.profile_image = new_image
        if current_user.role == "client":
            profile.organization_name = form.organization_name.data.strip() or None
            profile.website = form.website.data.strip() or None
            profile.description = form.bio.data.strip() or None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            remove_uploaded_file(new_image)
            current_app.logger.exception("Profile update failed for user %s", current_user.id)
            flash("We could not save your profile. Please try again.", "danger")
            return render_template("dashboard/profile.html", form=form, role=current_user.role), 500
        if old_image and new_image and old_image != new_image:
            remove_uploaded_file(old_image)
        flash("Profile saved.", "success")
        return redirect(url_for("dashboard.profile"))
    return render_template("dashboard/profile.html", form=form, role=current_user.role)


@dashboard_bp.route("/portfolio/new", methods=["GET", "POST"])
@roles_required("freelancer")
def portfolio_new():
    form = PortfolioForm()
    if form.validate_on_submit():
        upload = form.image.data
        image_path = store_image(upload) if upload and upload.filename else None
        item = Portfolio(freelancer_id=current_user.freelancer_profile.id, title=form.title.data.strip(), description=form.description.data.strip() or None, project_url=form.project_url.data.strip() or None, image_path=image_path)
        db.session.add(item)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            remove_uploaded_file(image_path)
            current_app.logger.exception("Portfolio creation failed for user %s", current_user.id)
            flash("We could not add that portfolio project. Please try again.", "danger")
            return render_template("dashboard/portfolio_form.html", form=form), 500
        flash("Portfolio project added.", "success")
        return redirect(url_for("marketplace.freelancer_detail", user_id=current_user.id))
    return render_template("dashboard/portfolio_form.html", form=form)


@dashboard_bp.post("/portfolio/<int:item_id>/delete")
@roles_required("freelancer")
def portfolio_delete(item_id):
    item = db.get_or_404(Portfolio, item_id)
    if item.freelancer_id != current_user.freelancer_profile.id:
        abort(403)
    image_path = item.image_path
    db.session.delete(item)
    db.session.commit()
    remove_uploaded_file(image_path)
    flash("Portfolio project removed.", "success")
    return redirect(url_for("marketplace.freelancer_detail", user_id=current_user.id))


@dashboard_bp.get("/applications")
@roles_required("client")
def applications():
    items = JobApplication.query.options(joinedload(JobApplication.job), joinedload(JobApplication.freelancer)).join(Job).filter(Job.client_id == current_user.id).order_by(JobApplication.created_at.desc()).all()
    return render_template("dashboard/applications.html", applications=items)


@dashboard_bp.get("/my-applications")
@roles_required("freelancer")
def my_applications():
    items = JobApplication.query.options(joinedload(JobApplication.job)).filter_by(freelancer_id=current_user.id).order_by(JobApplication.created_at.desc()).all()
    return render_template("dashboard/my_applications.html", applications=items)


@dashboard_bp.post("/applications/<int:application_id>/<action>")
@login_required
def application_action(application_id, action):
    application = db.get_or_404(JobApplication, application_id)
    if action not in ("shortlist", "reject", "withdraw"):
        abort(404)
    if action == "withdraw":
        if current_user.role != "freelancer" or application.freelancer_id != current_user.id or application.status not in ("pending", "shortlisted"):
            abort(403)
        application.status = "withdrawn"
        db.session.add(Notification(
            user_id=application.job.client_id,
            kind="application_withdrawn",
            message=f"{current_user.full_name} withdrew their application for {application.job.title}.",
            target_url=url_for("dashboard.applications"),
        ))
    else:
        if current_user.role != "client" or application.job.client_id != current_user.id or application.status not in ("pending", "shortlisted"):
            abort(403)
        application.status = "shortlisted" if action == "shortlist" else "rejected"
        db.session.add(Notification(user_id=application.freelancer_id, kind=f"application_{action}", message=f"Your application for {application.job.title} was {action}ed.", target_url=url_for("dashboard.index")))
    db.session.commit()
    flash("Application updated.", "success")
    return redirect(url_for("dashboard.applications") if current_user.role == "client" else url_for("dashboard.index"))


@dashboard_bp.get("/projects")
@login_required
def projects():
    options = (joinedload(Project.job), joinedload(Project.client), joinedload(Project.freelancer))
    if current_user.role == "admin":
        items = Project.query.options(*options).order_by(Project.updated_at.desc()).limit(200).all()
    else:
        items = Project.query.options(*options).filter((Project.client_id == current_user.id) | (Project.freelancer_id == current_user.id)).order_by(Project.updated_at.desc()).limit(100).all()
    return render_template("dashboard/projects.html", projects=items)


@dashboard_bp.route("/settings/notifications", methods=["GET", "POST"])
@login_required
def notification_settings():
    if request.method == "POST":
        for category in notifications_service.CATEGORIES:
            for channel in notifications_service.CHANNELS:
                field = f"{channel}_{category}"
                notifications_service.set_preference(
                    current_user.id, category, field in request.form, channel=channel)
                
        db.session.commit()
        flash("Notification preferences saved.", "success")
        return redirect(url_for("dashboard.notification_settings"))
    return render_template(
        "dashboard/notification_settings.html",
        preferences=notifications_service.preferences_for(current_user.id),
        labels=notifications_service.CATEGORY_LABELS,
    )


@dashboard_bp.get("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(100).all()
    return render_template("dashboard/notifications.html", notifications=items)


@dashboard_bp.get("/notifications/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    item = Notification.query.filter_by(id=notification_id, user_id=current_user.id).first_or_404()
    if item.read_at is None:
        item.read_at = datetime.now(timezone.utc)
        db.session.commit()
    target = item.target_url or url_for("dashboard.index")
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("dashboard.index")
    return redirect(target)


@dashboard_bp.get("/messages")
@login_required
def messages_inbox():
    project_options = (joinedload(Project.job), joinedload(Project.client), joinedload(Project.freelancer))
    if current_user.role == "admin":
        projects = Project.query.options(*project_options).order_by(Project.updated_at.desc()).limit(100).all()
    else:
        projects = Project.query.options(*project_options).filter(
            (Project.client_id == current_user.id) | (Project.freelancer_id == current_user.id)
        ).order_by(Project.updated_at.desc()).limit(100).all()
    project_ids = [project.id for project in projects]
    conversations = []
    if project_ids:
        latest_by_project = (
            select(Message.project_id, func.max(Message.created_at).label("latest_at"))
            .where(Message.project_id.in_(project_ids))
            .group_by(Message.project_id)
            .subquery()
        )
        latest_messages = Message.query.options(joinedload(Message.sender)).join(
            latest_by_project,
            and_(Message.project_id == latest_by_project.c.project_id,
                 Message.created_at == latest_by_project.c.latest_at),
        ).order_by(Message.id.desc()).all()
        latest_by_id = {}
        for message in latest_messages:
            latest_by_id.setdefault(message.project_id, message)
        unread_counts = dict(db.session.query(Message.project_id, func.count(Message.id)).filter(
            Message.project_id.in_(project_ids), Message.sender_id != current_user.id,
            Message.read_at.is_(None),
        ).group_by(Message.project_id).all())
        conversations = [
            (project, latest_by_id[project.id], unread_counts.get(project.id, 0))
            for project in projects if project.id in latest_by_id
        ]
    return render_template("dashboard/messages.html", conversations=conversations)


@dashboard_bp.get("/earnings")
@login_required
def earnings():
    if current_user.role == "freelancer":
        role_column = Transaction.payee_id
        title = "Earnings"
        description = "Escrow payments released to you, plus any directly recorded payments."
    elif current_user.role == "client":
        role_column = Transaction.payer_id
        title = "Project spending"
        description = "Amounts you funded, paid out to freelancers, or had refunded. Held amounts are not counted as paid yet."
    else:
        abort(403)
    transactions = Transaction.query.filter(role_column == current_user.id).order_by(Transaction.created_at.desc()).limit(100).all()
    totals = db.session.query(Transaction.currency, Transaction.status, db.func.sum(Transaction.amount)).filter(
        role_column == current_user.id
    ).group_by(Transaction.currency, Transaction.status).all()
    return render_template("dashboard/earnings.html", transactions=transactions, totals=totals,
                           title=title, description=description)


@dashboard_bp.get("/admin/payments")
@roles_required("admin")
def admin_payments():
    transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(200).all()
    totals = db.session.query(Transaction.currency, Transaction.status, db.func.sum(Transaction.amount)).group_by(
        Transaction.currency, Transaction.status
    ).all()
    held = db.session.query(Transaction.currency, db.func.sum(Transaction.amount)).filter_by(
        status="held"
    ).group_by(Transaction.currency).all()
    return render_template("dashboard/admin_payments.html", transactions=transactions, totals=totals, held=held)


@dashboard_bp.get("/admin/users")
@roles_required("admin")
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("dashboard/admin_users.html", users=users)


@dashboard_bp.get("/admin/jobs")
@roles_required("admin")
def admin_jobs():
    jobs = Job.query.order_by(Job.created_at.desc()).all()
    return render_template("dashboard/admin_jobs.html", jobs=jobs)


@dashboard_bp.post("/admin/jobs/<int:job_id>/toggle")
@roles_required("admin")
def toggle_job(job_id):
    job = db.get_or_404(Job, job_id)
    if job.status == "open":
        job.status = "cancelled"
    elif job.status == "cancelled":
        job.status = "open"
    else:
        flash("Only open or cancelled jobs can be changed here.", "warning")
        return redirect(url_for("dashboard.admin_jobs"))
    db.session.commit()
    flash("Job status updated.", "success")
    return redirect(url_for("dashboard.admin_jobs"))


@dashboard_bp.get("/admin/applications")
@roles_required("admin")
def admin_applications():
    applications = JobApplication.query.order_by(JobApplication.created_at.desc()).limit(200).all()
    return render_template("dashboard/admin_applications.html", applications=applications)


@dashboard_bp.post("/admin/users/<int:user_id>/toggle")
@roles_required("admin")
def toggle_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot suspend your own administrator account.", "warning")
    else:
        user.is_active = not user.is_active
        db.session.commit()
        flash(f"Account {'activated' if user.is_active else 'suspended'}.", "success")
    return redirect(url_for("dashboard.admin_users"))


@dashboard_bp.get("/admin/reports")
@roles_required("admin")
def admin_reports():
    reports = Report.query.order_by(Report.created_at.desc()).all()
    return render_template("dashboard/admin_reports.html", reports=reports)


@dashboard_bp.route("/reports/<int:user_id>/new", methods=["GET", "POST"])
@login_required
def report_user(user_id):
    target = User.query.filter_by(id=user_id, is_active=True).first_or_404()
    if target.id == current_user.id:
        abort(400)
    form = ReportForm()
    if form.validate_on_submit():
        report = Report(reporter_id=current_user.id, reported_user_id=target.id, reason=form.reason.data, details=form.details.data.strip() or None)
        db.session.add(report)
        for admin in User.query.filter_by(role="admin", is_active=True).all():
            db.session.add(Notification(
                user_id=admin.id,
                kind="member_report",
                message=f"A member reported {target.full_name} for {form.reason.data}.",
                target_url=url_for("dashboard.admin_reports"),
            ))
        db.session.commit()
        flash("Your report was sent to the SkillLink team.", "success")
        return redirect(url_for("main.index"))
    return render_template("dashboard/report_form.html", form=form, target=target)


@dashboard_bp.post("/admin/reports/<int:report_id>/<action>")
@roles_required("admin")
def resolve_report(report_id, action):
    if action not in ("resolve", "dismiss"):
        abort(404)
    report = db.get_or_404(Report, report_id)
    report.status = "resolved" if action == "resolve" else "dismissed"
    report.resolved_by = current_user.id
    report.resolved_at = datetime.now(timezone.utc)
    db.session.commit()
    flash("Report updated.", "success")
    return redirect(url_for("dashboard.admin_reports"))
