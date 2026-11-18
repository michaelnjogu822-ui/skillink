from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms import LoginForm, RegisterForm
from app.models import ClientProfile, FreelancerProfile, User
from app.security import equalize_login_timing

auth_bp = Blueprint("auth", __name__)

LOCKED_MESSAGE = "Too many failed sign-in attempts. Try again in a few minutes."


def login_throttle():
    return current_app.extensions["login_throttle"]


def attempt_key(email):
    return (request.remote_addr or "unknown", (email or "").strip().lower())


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        if User.query.filter_by(email=email).first():
            form.email.errors.append("An account with this email already exists.")
        else:
            user = User(email=email, full_name=form.full_name.data.strip(), role=form.role.data)
            user.set_password(form.password.data)
            if user.role == "freelancer":
                user.freelancer_profile = FreelancerProfile()
            else:
                user.client_profile = ClientProfile()
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.email.errors.append("An account with this email already exists.")
                return render_template("auth/register.html", form=form)
            login_user(user)
            flash("Welcome to SkillLink. Complete your profile to get started.", "success")
            return redirect(url_for("dashboard.profile"))
    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        throttle = login_throttle()
        key = attempt_key(email)
        if throttle.is_locked(key):
            flash(LOCKED_MESSAGE, "danger")
            response = current_app.make_response(render_template("auth/login.html", form=form))
            response.status_code = 429
            response.headers["Retry-After"] = str(throttle.retry_after(key))
            return response
        user = User.query.filter_by(email=email).first()
        if user is None:
            # Unknown emails must take as long as known ones, or the response
            # time reveals which addresses have accounts.
            equalize_login_timing(form.password.data)
        if user and user.is_active and user.check_password(form.password.data):
            throttle.reset(key)
            session.clear()
            login_user(user, remember=form.remember.data, fresh=True)
            flash("You are signed in.", "success")
            return redirect(url_for("dashboard.index"))
        throttle.record_failure(key)
        flash("We couldn't sign you in with those details.", "danger")
    return render_template("auth/login.html", form=form)


@auth_bp.post("/logout")
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.index"))
