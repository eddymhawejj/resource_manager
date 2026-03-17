from flask import Blueprint

bp = Blueprint('monitoring', __name__, url_prefix='/monitoring')

from app.monitoring import routes  # noqa: E402, F401
