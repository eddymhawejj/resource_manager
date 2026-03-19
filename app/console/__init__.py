from flask import Blueprint

bp = Blueprint('console', __name__, url_prefix='/console')

from app.console import routes  # noqa: E402, F401
