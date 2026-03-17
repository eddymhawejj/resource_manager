from flask import Blueprint

bp = Blueprint('bookings', __name__, url_prefix='/bookings')

from app.bookings import routes  # noqa: E402, F401
