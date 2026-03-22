from datetime import datetime, timedelta, timezone

from flask import render_template, jsonify, abort, request
from flask_login import login_required
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.monitoring import bp
from app.extensions import db
from app.models import Resource, ResourceHost, PingResult, Booking, MaintenanceWindow


def _prefetch_recent_pings(hosts, limit=30):
    """Batch-load recent ping results for a list of hosts.

    Avoids N+1 queries by fetching pings for all hosts in a single query,
    then attaching them to each host as _recent_pings.
    """
    if not hosts:
        return
    host_ids = [h.id for h in hosts]
    host_map = {h.id: h for h in hosts}

    for h in hosts:
        h._recent_pings = []

    # Use a window function to rank pings per host and keep only the most recent N
    ranked = (
        db.session.query(PingResult)
        .filter(PingResult.host_id.in_(host_ids))
        .filter(PingResult.host_id.isnot(None))
        .order_by(PingResult.host_id, PingResult.checked_at.desc())
        .all()
    )

    # Group by host_id, keeping only `limit` per host
    counts = {}
    for pr in ranked:
        hid = pr.host_id
        counts[hid] = counts.get(hid, 0) + 1
        if counts[hid] <= limit:
            host_map[hid]._recent_pings.append(pr)


@bp.route('/status/<int:resource_id>')
@login_required
def resource_status(resource_id):
    """HTMX partial: returns a status badge for a resource."""
    resource = (
        Resource.query
        .filter_by(id=resource_id)
        .options(selectinload(Resource.hosts), selectinload(Resource.children).selectinload(Resource.hosts))
        .first()
    ) or abort(404)

    # Pre-fetch recent pings for status computation (only need 3 per host)
    all_hosts = list(resource.hosts)
    for child in resource.children:
        all_hosts.extend(child.hosts)
    _prefetch_recent_pings(all_hosts, limit=3)

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
        .options(selectinload(Resource.hosts))
        .order_by(Resource.name)
        .all()
    )

    # Batch pre-fetch recent pings for all hosts (1 query instead of N per host)
    all_hosts = []
    for r in resources:
        all_hosts.extend(r.hosts)
    _prefetch_recent_pings(all_hosts, limit=30)

    return render_template('monitoring/dashboard.html', resources=resources)


def _fetch_ping_results_by_host(host_ids, since):
    """Fetch ping results for multiple hosts since a date, grouped by host_id.

    Returns dict of {host_id: [PingResult, ...]} ordered by checked_at asc.
    Single query instead of N queries.
    """
    if not host_ids:
        return {}
    results = (
        PingResult.query
        .filter(PingResult.host_id.in_(host_ids))
        .filter(PingResult.checked_at >= since)
        .order_by(PingResult.host_id, PingResult.checked_at.asc())
        .all()
    )
    grouped = {hid: [] for hid in host_ids}
    for r in results:
        grouped[r.host_id].append(r)
    return grouped


@bp.route('/health/<int:resource_id>')
@login_required
def health_history(resource_id):
    """Health history page: uptime percentage over time for a resource."""
    resource = (
        Resource.query.filter_by(id=resource_id)
        .options(selectinload(Resource.hosts))
        .first()
    ) or abort(404)
    days = request.args.get('days', 7, type=int)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    hosts = list(resource.hosts)
    host_ids = [h.id for h in hosts]
    pings_by_host = _fetch_ping_results_by_host(host_ids, since)

    host_data = []
    overall_total = 0
    overall_up = 0

    for host in hosts:
        results = pings_by_host.get(host.id, [])
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
    resource = (
        Resource.query.filter_by(id=resource_id)
        .options(selectinload(Resource.hosts))
        .first()
    ) or abort(404)
    days = request.args.get('days', 7, type=int)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    hosts = list(resource.hosts)
    host_ids = [h.id for h in hosts]
    pings_by_host = _fetch_ping_results_by_host(host_ids, since)

    data = {}
    for host in hosts:
        data[host.label or host.address] = [
            {
                'time': r.checked_at.isoformat(),
                'reachable': r.is_reachable,
                'response_time': r.response_time_ms,
            }
            for r in pings_by_host.get(host.id, [])
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

    # Batch-load all relevant bookings in one query instead of N
    testbed_ids = [tb.id for tb in testbeds]
    all_bookings = Booking.query.filter(
        Booking.resource_id.in_(testbed_ids),
        Booking.status == 'confirmed',
        Booking.end_time >= since,
    ).all() if testbed_ids else []

    # Group by resource_id
    bookings_by_resource = {}
    for b in all_bookings:
        bookings_by_resource.setdefault(b.resource_id, []).append(b)

    resource_stats = []
    for tb in testbeds:
        bookings = bookings_by_resource.get(tb.id, [])

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
