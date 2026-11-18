"""SkillLink application factory."""
import os

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask.cli import with_appcontext
import click
from flask_login import current_user
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from email_validator import EmailNotValidError, validate_email

from config import DevelopmentConfig, ProductionConfig
from app.extensions import csrf, db, login_manager, migrate


def create_app(config_object=None, config_overrides=None):
    app = Flask(__name__, instance_relative_config=True)
    if config_object is None:
        config_object = ProductionConfig if os.environ.get("APP_ENV", "development").lower() == "production" else DevelopmentConfig
    app.config.from_object(config_object)
    if config_overrides:
        app.config.update(config_overrides)
    is_production = isinstance(config_object, type) and issubclass(config_object, ProductionConfig)
    if is_production:
        secret = app.config.get("SECRET_KEY", "")
        database_url = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if len(secret) < 32 or secret == "dev-only-change-me":
            raise RuntimeError("Production SECRET_KEY must contain at least 32 characters.")
        if not database_url or database_url.startswith("sqlite:"):
            raise RuntimeError("Production requires DATABASE_URL for a supported server database; SQLite is for local development only.")
        if not app.config.get("SESSION_COOKIE_SECURE"):
            raise RuntimeError("Production requires SESSION_COOKIE_SECURE=true and HTTPS.")
    os.makedirs(app.instance_path, exist_ok=True)
    upload_folder = app.config.get("UPLOAD_FOLDER") or ""
    if not upload_folder:
        upload_folder = os.path.join(app.instance_path, "uploads")
    elif not os.path.isabs(upload_folder):
        upload_folder = os.path.abspath(upload_folder)
    app.config["UPLOAD_FOLDER"] = upload_folder
    os.makedirs(upload_folder, exist_ok=True)

    from app.security import LoginThrottle
    app.extensions["login_throttle"] = LoginThrottle(
        max_failures=app.config.get("LOGIN_MAX_FAILURES", 5),
        window_seconds=app.config.get("LOGIN_WINDOW_SECONDS", 300),
    )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

    # Import models before registering migrations so Alembic can discover metadata.
    from app import models  # noqa: F401
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.marketplace import marketplace_bp
    from app.routes.dashboard import dashboard_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(marketplace_bp)
    app.register_blueprint(dashboard_bp)

    @app.context_processor
    def inject_unread_notification_count():
        count = 0
        unread_message_count = 0
        if current_user.is_authenticated:
            from app.models import Message, Notification, Project
            unread_notifications = db.select(func.count(Notification.id)).where(
                Notification.user_id == current_user.id, Notification.read_at.is_(None)
            ).scalar_subquery()
            unread_messages = db.select(func.count(Message.id)).join(
                Project, Project.id == Message.project_id
            ).where(
                ((Project.client_id == current_user.id) | (Project.freelancer_id == current_user.id)),
                Message.sender_id != current_user.id, Message.read_at.is_(None),
            ).scalar_subquery()
            count, unread_message_count = db.session.query(unread_notifications, unread_messages).one()
        return {"unread_notification_count": count, "unread_message_count": unread_message_count}

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.argument("full_name")
    @click.password_option()
    @with_appcontext
    def create_admin(email, full_name, password):
        """Create an administrator account without public admin registration."""
        from app.extensions import db
        from app.models import User
        email = email.strip().lower()
        try:
            email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise click.ClickException("Enter a valid email address.") from exc
        if len(password) < 12 or not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
            raise click.ClickException("Admin passwords must be 12+ characters and include a letter and a number.")
        if not 2 <= len(full_name.strip()) <= 120:
            raise click.ClickException("Administrator name must be between 2 and 120 characters.")
        if User.query.filter_by(email=email).first():
            raise click.ClickException("That email address is already registered.")
        user = User(email=email, full_name=full_name.strip(), role="admin")
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            raise click.ClickException("An account with that email address was just created.") from exc
        click.echo(f"Administrator created: {user.email}")

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            db.session.rollback()
            app.logger.exception("SkillLink database readiness check failed")
            return jsonify(status="unavailable", service="SkillLink", database="disconnected"), 503
        return jsonify(status="ok", service="SkillLink", database="connected")

    @app.get("/uploads/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/error.html", code=403, title="You can’t access this page", message="This area is only available to the account roles that manage it."), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/error.html", code=404, title="We couldn’t find that page", message="The page may have moved, or the link may no longer be available."), 404

    @app.errorhandler(413)
    def upload_too_large(_error):
        return render_template("errors/error.html", code=413, title="That file is too large", message="Uploads must be 5 MB or smaller. Choose a smaller file and try again."), 413

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; object-src 'none'; "
            "img-src 'self' data:; font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "script-src 'self' https://cdn.jsdelivr.net",
        )
        if app.config.get("SESSION_COOKIE_SECURE") and request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    return app
