from flask import Blueprint

bp = Blueprint('network', __name__, url_prefix='/network')

from app.network import routes  # noqa: E402, F401
