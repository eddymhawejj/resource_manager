from flask import render_template, jsonify, abort
from flask_login import login_required

from app.monitoring import bp
from app.extensions import db
from app.models import Resource, PingResult


@bp.route('/status/<int:resource_id>')
@login_required
def resource_status(resource_id):
    """HTMX partial: returns a status badge for a resource."""
    resource = db.session.get(Resource, resource_id) or abort(404)
    return render_template('resources/_status_badge.html', resource=resource)


@bp.route('/history/<int:resource_id>')
@login_required
def ping_history(resource_id):
    """Return ping history as JSON for charts."""
    resource = db.session.get(Resource, resource_id) or abort(404)
    results = (
        PingResult.query
        .filter_by(resource_id=resource_id)
        .order_by(PingResult.checked_at.desc())
        .limit(50)
        .all()
    )
    return jsonify([
        {
            'time': r.checked_at.isoformat(),
            'reachable': r.is_reachable,
            'response_time': r.response_time_ms,
            'resolved_ip': r.resolved_ip,
        }
        for r in reversed(results)
    ])


@bp.route('/dashboard')
@login_required
def dashboard():
    """Monitoring overview page."""
    resources = Resource.query.filter(
        Resource.ip_address.isnot(None),
        Resource.ip_address != '',
        Resource.is_active.is_(True),
    ).order_by(Resource.name).all()
    return render_template('monitoring/dashboard.html', resources=resources)
