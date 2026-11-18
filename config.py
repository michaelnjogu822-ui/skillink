"""Application configuration loaded from the environment."""
import os
from datetime import timedelta


def env_flag(name, default="false"):
    return os.environ.get(name, default).lower() == "true"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or "sqlite:///skilllink.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pool_recycle keeps MySQL from handing out connections the server already
    # closed (wait_timeout is 8 hours by default on MySQL 8).
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    # Empty means "use <instance>/uploads"; the app factory resolves it so the
    # location does not depend on the directory the server was started from.
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_flag("SESSION_COOKIE_SECURE")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    # Number of reverse proxies in front of the app. Setting this makes Flask
    # honour X-Forwarded-For / X-Forwarded-Proto from exactly that many hops,
    # which the rate limiter needs to see the real client address.
    TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
    LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
    # Application-layer firewall (app/services/firewall.py).
    FIREWALL_ENABLED = env_flag("FIREWALL_ENABLED", "true")
    RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
    RATE_LIMIT_GLOBAL = int(os.environ.get("RATE_LIMIT_GLOBAL", "300"))
    RATE_LIMIT_WRITE = int(os.environ.get("RATE_LIMIT_WRITE", "60"))
    RATE_LIMIT_AUTH = int(os.environ.get("RATE_LIMIT_AUTH", "20"))
    ALLOWED_HOSTS = tuple(
        host.strip().lower()
        for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
        if host.strip()
    )
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
    MAIL_USE_TLS = env_flag("MAIL_USE_TLS", "true")
    MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "10"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    # A production deployment always sits behind TLS termination, so links
    # generated outside a request (CLI, email) must be https.
    SERVER_NAME = os.environ.get("SERVER_NAME") or None
