from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms import LoginForm, RegisterForm
from app.models import ClientProfile, FreelancerProfile, User

auth_bp = Blueprint("auth", __name__)


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
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and user.is_active and user.check_password(form.password.data):
            login_user(user, remember=form.remember.data, fresh=True)
            flash("You are signed in.", "success")
            return redirect(url_for("dashboard.index"))
        flash("We couldn't sign you in with those details.", "danger")
    return render_template("auth/login.html", form=form)


@auth_bp.post("/logout")
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.index"))
