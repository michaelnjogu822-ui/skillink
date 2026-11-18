"""WSGI entry point for production servers (gunicorn, waitress, uWSGI)."""
from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402  (dotenv must load before config import)

app = create_app()
application = app
