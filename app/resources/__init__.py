from flask import Blueprint

bp = Blueprint('resources', __name__, url_prefix='/resources')

from app.resources import routes  # noqa: E402, F401
