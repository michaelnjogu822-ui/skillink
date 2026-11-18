"""Application configuration loaded from the environment."""
import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or "sqlite:///skilllink.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    # Empty means "use <instance>/uploads"; the app factory resolves it so the
    # location does not depend on the directory the server was started from.
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
    LOGIN_WINDOW_SECONDS = int(os.environ.get("LOGIN_WINDOW_SECONDS", "300"))
    # Payment provider used by the escrow service. Only "simulated" ships with
    # the project; a real provider registers itself in app/services/escrow.py.
    PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "simulated")
    # Transactional email. "console" prints to the log, "smtp" sends for real,
    # "memory" captures messages for tests.
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "SkillLink <no-reply@skilllink.local>")
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "10"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
