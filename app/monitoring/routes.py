from datetime import datetime, timedelta, timezone

from flask import render_template, jsonify, abort, request
from flask_login import login_required

from app.monitoring import bp
from app.extensions import db
from app.models import Resource, ResourceHost, PingResult, Booking, MaintenanceWindow
from sqlalchemy import func


@bp.route('/status/<int:resource_id>')
@login_required
def resource_status(resource_id):
    """HTMX partial: returns a status badge for a resource."""
    resource = db.session.get(Resource, resource_id) or abort(404)
    in_maintenance = MaintenanceWindow.resource_in_maintenance(resource_id)
    return render_template('resources/_status_badge.html', resource=resource, in_maintenance=in_maintenance)


@bp.route('/history/<int:host_id>')
@login_required
def ping_history(host_id):
    """Return ping history for a host as JSON for charts."""
    host = db.session.get(ResourceHost, host_id) or abort(404)
    results = (
        PingResult.query
        .filter_by(host_id=host_id)
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
    resources = (
        Resource.query
        .filter(Resource.is_active.is_(True))
        .filter(Resource.resource_type != 'device')
        .filter(Resource.hosts.any())
        .order_by(Resource.name)
        .all()
    )
    return render_template('monitoring/dashboard.html', resources=resources)


@bp.route('/health/<int:resource_id>')
@login_required
def health_history(resource_id):
    """Health history page: uptime percentage over time for a resource."""
    resource = db.session.get(Resource, resource_id) or abort(404)
    days = request.args.get('days', 7, type=int)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    hosts = resource.hosts.all()
    host_data = []
    overall_total = 0
    overall_up = 0

    for host in hosts:
        results = (
            PingResult.query
            .filter_by(host_id=host.id)
            .filter(PingResult.checked_at >= since)
            .order_by(PingResult.checked_at.asc())
            .all()
        )
        total = len(results)
        up = sum(1 for r in results if r.is_reachable)
        uptime_pct = (up / total * 100) if total > 0 else 0
        overall_total += total
        overall_up += up

        host_data.append({
            'host': host,
            'total': total,
            'up': up,
            'uptime_pct': round(uptime_pct, 1),
            'results': results,
        })

    overall_uptime = (overall_up / overall_total * 100) if overall_total > 0 else 0

    return render_template('monitoring/health_history.html',
                           resource=resource, host_data=host_data,
                           overall_uptime=round(overall_uptime, 1),
                           days=days)


@bp.route('/health/<int:resource_id>/data')
@login_required
def health_data(resource_id):
    """JSON uptime data for chart rendering."""
    resource = db.session.get(Resource, resource_id) or abort(404)
    days = request.args.get('days', 7, type=int)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    hosts = resource.hosts.all()
    data = {}
    for host in hosts:
        results = (
            PingResult.query
            .filter_by(host_id=host.id)
            .filter(PingResult.checked_at >= since)
            .order_by(PingResult.checked_at.asc())
            .all()
        )
        data[host.label or host.address] = [
            {
                'time': r.checked_at.isoformat(),
                'reachable': r.is_reachable,
                'response_time': r.response_time_ms,
            }
            for r in results
        ]

    return jsonify(data)


@bp.route('/analytics')
@login_required
def usage_analytics():
    """Usage analytics dashboard: utilization rates per resource."""
    days = request.args.get('days', 30, type=int)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    total_hours = days * 24

    testbeds = Resource.query.filter_by(parent_id=None, is_active=True).filter(
        Resource.resource_type != 'device'
    ).order_by(Resource.name).all()

    resource_stats = []
    for tb in testbeds:
        bookings = Booking.query.filter(
            Booking.resource_id == tb.id,
            Booking.status == 'confirmed',
            Booking.end_time >= since,
        ).all()

        booked_hours = 0
        for b in bookings:
            start = max(b.start_time, since.replace(tzinfo=None) if b.start_time.tzinfo is None else since)
            end = min(b.end_time, datetime.now(timezone.utc).replace(tzinfo=None) if b.end_time.tzinfo is None else datetime.now(timezone.utc))
            if end > start:
                booked_hours += (end - start).total_seconds() / 3600

        utilization = (booked_hours / total_hours * 100) if total_hours > 0 else 0
        booking_count = len(bookings)
        unique_users = len(set(b.user_id for b in bookings))

        resource_stats.append({
            'resource': tb,
            'booked_hours': round(booked_hours, 1),
            'utilization': round(utilization, 1),
            'booking_count': booking_count,
            'unique_users': unique_users,
        })

    # Sort by utilization descending
    resource_stats.sort(key=lambda x: x['utilization'], reverse=True)

    # Overall stats
    total_bookings = Booking.query.filter(
        Booking.status == 'confirmed',
        Booking.end_time >= since,
    ).count()
    total_users = db.session.query(func.count(func.distinct(Booking.user_id))).filter(
        Booking.status == 'confirmed',
        Booking.end_time >= since,
    ).scalar() or 0

    return render_template('monitoring/analytics.html',
                           resource_stats=resource_stats,
                           days=days, total_bookings=total_bookings,
                           total_users=total_users)
