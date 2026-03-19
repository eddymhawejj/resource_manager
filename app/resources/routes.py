import re
from datetime import datetime, timezone

from flask import render_template, redirect, url_for, flash, abort, request, Response, jsonify
from flask_login import login_required, current_user

from app.resources import bp
from app.resources.forms import ResourceForm, ChildResourceForm, HostForm
from app.extensions import db
from app.models import (Resource, ResourceHost, ResourceAssignment, AuditLog, Tag, Favorite,
                        MaintenanceWindow, AlertRule, AccessPoint, Booking, can_user_access)


def _is_valid_host(value):
    """Check if a string is a valid IPv4 address or hostname."""
    value = value.strip()
    if not value:
        return False
    ipv4 = re.match(r'^(\d{1,3}\.){3}\d{1,3}$', value)
    if ipv4:
        return all(0 <= int(p) <= 255 for p in value.split('.'))
    hostname_re = re.compile(r'^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*$')
    return bool(hostname_re.match(value))


def _sync_hosts_from_form(resource):
    """Replace a resource's hosts with the data from the submitted form arrays."""
    addresses = request.form.getlist('host_addresses[]')
    labels = request.form.getlist('host_labels[]')
    critical_values = request.form.getlist('host_critical[]')

    # The critical checkboxes use a hidden+checkbox pattern:
    # each host produces a hidden "0" and optionally a checked "1".
    # Parse pairs: for each host, consume values until we build the list.
    critical_flags = []
    idx = 0
    for i in range(len(addresses)):
        # Each host has at least a hidden "0"
        if idx < len(critical_values) and critical_values[idx] == '0':
            idx += 1
            # If next value is "1", the checkbox was checked
            if idx < len(critical_values) and critical_values[idx] == '1':
                critical_flags.append(True)
                idx += 1
            else:
                critical_flags.append(False)
        elif idx < len(critical_values) and critical_values[idx] == '1':
            critical_flags.append(True)
            idx += 1
        else:
            critical_flags.append(True)

    # Delete existing hosts
    for host in resource.hosts.all():
        db.session.delete(host)

    # Add new hosts (skip empty rows and invalid addresses)
    errors = []
    for i, addr in enumerate(addresses):
        addr = addr.strip()
        if not addr:
            continue
        if not _is_valid_host(addr):
            errors.append(f'"{addr}" is not a valid IP address or hostname.')
            continue
        label = labels[i].strip() if i < len(labels) else ''
        critical = critical_flags[i] if i < len(critical_flags) else True
        host = ResourceHost(
            resource_id=resource.id,
            address=addr,
            label=label,
            critical=critical,
        )
        host.auto_link_subnet()
        db.session.add(host)

    for err in errors:
        flash(err, 'danger')


def admin_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)

    return decorated


@bp.route('/')
@login_required
def list_resources():
    tag_filter = request.args.get('tag', '')
    query = Resource.query.filter_by(parent_id=None).filter(
        Resource.resource_type != 'device'
    )
    if tag_filter:
        query = query.filter(Resource.tags.any(Tag.name == tag_filter))
    testbeds = query.order_by(Resource.name).all()
    all_tags = Tag.query.order_by(Tag.name).all()

    # Get user's favorite resource IDs
    fav_ids = set()
    if current_user.is_authenticated:
        fav_ids = {f.resource_id for f in Favorite.query.filter_by(user_id=current_user.id).all()}

    return render_template('resources/list.html', testbeds=testbeds, all_tags=all_tags,
                           current_tag=tag_filter, fav_ids=fav_ids)


@bp.route('/<int:resource_id>')
@login_required
def detail(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    children = Resource.query.filter_by(parent_id=resource_id).order_by(Resource.name).all()
    host_form = HostForm()
    is_favorited = Favorite.query.filter_by(user_id=current_user.id, resource_id=resource_id).first() is not None
    active_maintenance = MaintenanceWindow.query.filter(
        MaintenanceWindow.resource_id == resource_id,
        MaintenanceWindow.end_time >= datetime.now(timezone.utc),
    ).order_by(MaintenanceWindow.start_time).all()
    alert_rules = AlertRule.query.filter_by(resource_id=resource_id).all()
    all_tags = Tag.query.order_by(Tag.name).all()

    # Shared children (via resource_assignments)
    shared_assignments = ResourceAssignment.query.filter_by(parent_id=resource_id).all()
    # Shared parents (testbeds this resource is assigned to)
    shared_parents = ResourceAssignment.query.filter_by(child_id=resource_id).all()
    # Available resources for assigning as shared children (exclude self, existing children, existing assignments)
    assignable_resources = []
    if resource.is_testbed and current_user.is_authenticated and current_user.is_admin:
        existing_child_ids = {c.id for c in children}
        existing_assignment_ids = {a.child_id for a in shared_assignments}
        exclude_ids = existing_child_ids | existing_assignment_ids | {resource_id}
        assignable_resources = Resource.query.filter(
            Resource.id.notin_(exclude_ids),
            Resource.resource_type != 'device',
        ).order_by(Resource.name).all()

    # Available testbeds for assigning as shared parents (from child's / device's view)
    assignable_parents = []
    if current_user.is_authenticated and current_user.is_admin:
        existing_parent_ids = {a.parent_id for a in shared_parents}
        if resource.parent_id:
            existing_parent_ids.add(resource.parent_id)
        existing_parent_ids.add(resource_id)
        assignable_parents = Resource.query.filter(
            Resource.id.notin_(existing_parent_ids),
            Resource.parent_id == None,
            Resource.id != resource_id,
        ).order_by(Resource.name).all()

    # Access points: own + children's (for testbeds)
    own_access_points = AccessPoint.query.filter_by(resource_id=resource_id, is_enabled=True).all()
    child_access_points = []
    if resource.is_testbed:
        child_ids = [c.id for c in children]
        child_ids += [a.child_id for a in shared_assignments]
        if child_ids:
            child_access_points = AccessPoint.query.filter(
                AccessPoint.resource_id.in_(child_ids), AccessPoint.is_enabled == True
            ).all()
    all_access_points = own_access_points + child_access_points
    all_access_points_admin = AccessPoint.query.filter_by(resource_id=resource_id).all() if current_user.is_admin else []

    # Check if user has an active booking for this testbed
    testbed_id = resource_id if resource.is_testbed else resource.parent_id
    has_active_booking = False
    if testbed_id:
        has_active_booking = Booking.user_has_active_booking(current_user.id, testbed_id)
    can_access = current_user.is_admin or has_active_booking

    # For device-type resources: list testbeds to promote into
    available_testbeds = []
    if resource.resource_type == 'device' and current_user.is_admin:
        available_testbeds = Resource.query.filter(
            Resource.parent_id == None,
            Resource.resource_type != 'device',
            Resource.id != resource_id,
        ).order_by(Resource.name).all()

    return render_template('resources/detail.html', resource=resource, children=children,
                           host_form=host_form, is_favorited=is_favorited,
                           active_maintenance=active_maintenance,
                           alert_rules=alert_rules, all_tags=all_tags,
                           shared_assignments=shared_assignments,
                           shared_parents=shared_parents,
                           assignable_resources=assignable_resources,
                           assignable_parents=assignable_parents,
                           all_access_points=all_access_points,
                           all_access_points_admin=all_access_points_admin,
                           has_active_booking=has_active_booking,
                           can_access=can_access,
                           available_testbeds=available_testbeds)


@bp.route('/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_resource():
    form = ResourceForm()
    if form.validate_on_submit():
        resource = Resource(
            name=form.name.data,
            description=form.description.data,
            resource_type=form.resource_type.data,
            location=form.location.data,
            is_active=form.is_active.data,
        )
        db.session.add(resource)
        db.session.flush()
        _sync_hosts_from_form(resource)
        # Handle tags
        tag_names = request.form.get('tags', '').split(',')
        _sync_tags(resource, tag_names)
        AuditLog.log('resource.create', 'resource', resource.id, {'name': resource.name}, user_id=current_user.id)
        db.session.commit()
        flash(f'Testbed "{resource.name}" created.', 'success')
        return redirect(url_for('resources.detail', resource_id=resource.id))
    return render_template('resources/form.html', form=form, title='Add Testbed', all_tags=Tag.query.order_by(Tag.name).all())


@bp.route('/<int:resource_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_resource(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    form = ResourceForm(obj=resource)
    if form.validate_on_submit():
        original_type = resource.resource_type
        form.populate_obj(resource)
        # Preserve 'device' type — it should only change via the promote flow
        if original_type == 'device' and resource.resource_type != original_type:
            resource.resource_type = original_type
        _sync_hosts_from_form(resource)
        tag_names = request.form.get('tags', '').split(',')
        _sync_tags(resource, tag_names)
        AuditLog.log('resource.update', 'resource', resource.id, {'name': resource.name}, user_id=current_user.id)
        db.session.commit()
        flash(f'Resource "{resource.name}" updated.', 'success')
        return redirect(url_for('resources.detail', resource_id=resource.id))
    return render_template('resources/form.html', form=form, title=f'Edit {resource.name}',
                           resource=resource, all_tags=Tag.query.order_by(Tag.name).all())


@bp.route('/<int:resource_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_resource(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    parent_id = resource.parent_id
    name = resource.name

    AuditLog.log('resource.delete', 'resource', resource_id, {'name': name}, user_id=current_user.id)
    db.session.delete(resource)
    db.session.commit()
    flash(f'Resource "{name}" deleted.', 'success')

    if parent_id:
        return redirect(url_for('resources.detail', resource_id=parent_id))
    return redirect(url_for('resources.list_resources'))


@bp.route('/<int:testbed_id>/children/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_child(testbed_id):
    testbed = db.session.get(Resource, testbed_id) or abort(404)
    if not testbed.is_testbed:
        abort(400)

    form = ChildResourceForm()
    if form.validate_on_submit():
        child = Resource(
            name=form.name.data,
            description=form.description.data,
            resource_type=form.resource_type.data,
            location=form.location.data,
            is_active=form.is_active.data,
            parent_id=testbed_id,
        )
        db.session.add(child)
        db.session.flush()
        _sync_hosts_from_form(child)
        db.session.commit()
        flash(f'Child resource "{child.name}" added to {testbed.name}.', 'success')
        return redirect(url_for('resources.detail', resource_id=testbed_id))
    return render_template('resources/form.html', form=form,
                           title=f'Add Child Resource to {testbed.name}', testbed=testbed)


@bp.route('/<int:resource_id>/hosts/add', methods=['POST'])
@login_required
@admin_required
def add_host(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    form = HostForm()
    if form.validate_on_submit():
        host = ResourceHost(
            resource_id=resource.id,
            address=form.address.data.strip(),
            label=form.label.data.strip() if form.label.data else '',
            critical=form.critical.data,
        )
        host.auto_link_subnet()
        db.session.add(host)
        db.session.commit()
        flash(f'Host "{host.address}" added.', 'success')
    else:
        for field, errors in form.errors.items():
            if field != 'csrf_token':
                for error in errors:
                    flash(f'{error}', 'danger')
    return redirect(url_for('resources.detail', resource_id=resource.id))


@bp.route('/<int:resource_id>/hosts/<int:host_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_host(resource_id, host_id):
    host = db.session.get(ResourceHost, host_id) or abort(404)
    if host.resource_id != resource_id:
        abort(404)
    address = host.address
    db.session.delete(host)
    db.session.commit()
    flash(f'Host "{address}" removed.', 'success')
    return redirect(url_for('resources.detail', resource_id=resource_id))


def _sync_tags(resource, tag_names):
    """Sync tags for a resource from a list of tag name strings."""
    resource.tags = []
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag = Tag.query.filter_by(name=name).first()
        if not tag:
            tag = Tag(name=name)
            db.session.add(tag)
            db.session.flush()
        resource.tags.append(tag)


# ===== Favorites =====
@bp.route('/<int:resource_id>/favorite', methods=['POST'])
@login_required
def toggle_favorite(resource_id):
    db.session.get(Resource, resource_id) or abort(404)
    existing = Favorite.query.filter_by(user_id=current_user.id, resource_id=resource_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        flash('Removed from favorites.', 'info')
    else:
        fav = Favorite(user_id=current_user.id, resource_id=resource_id)
        db.session.add(fav)
        db.session.commit()
        flash('Added to favorites.', 'success')
    return redirect(request.referrer or url_for('resources.list_resources'))


# ===== Maintenance Windows =====
@bp.route('/<int:resource_id>/maintenance/add', methods=['POST'])
@login_required
@admin_required
def add_maintenance(resource_id):
    db.session.get(Resource, resource_id) or abort(404)
    title = request.form.get('maint_title', '').strip()
    start = request.form.get('maint_start', '')
    end = request.form.get('maint_end', '')
    notes = request.form.get('maint_notes', '')

    if not title or not start or not end:
        flash('Title, start and end are required.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        flash('Invalid date format.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    mw = MaintenanceWindow(
        resource_id=resource_id,
        title=title,
        start_time=start_dt,
        end_time=end_dt,
        notes=notes,
        created_by=current_user.id,
    )
    db.session.add(mw)
    AuditLog.log('maintenance.create', 'maintenance', None, {'resource_id': resource_id, 'title': title}, user_id=current_user.id)
    db.session.commit()
    flash(f'Maintenance window "{title}" created.', 'success')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/maintenance/<int:mw_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_maintenance(resource_id, mw_id):
    mw = db.session.get(MaintenanceWindow, mw_id) or abort(404)
    if mw.resource_id != resource_id:
        abort(404)
    db.session.delete(mw)
    AuditLog.log('maintenance.delete', 'maintenance', mw_id, {'resource_id': resource_id}, user_id=current_user.id)
    db.session.commit()
    flash('Maintenance window removed.', 'info')
    return redirect(url_for('resources.detail', resource_id=resource_id))


# ===== Alert Rules =====
@bp.route('/<int:resource_id>/alerts/add', methods=['POST'])
@login_required
@admin_required
def add_alert(resource_id):
    db.session.get(Resource, resource_id) or abort(404)
    alert_type = request.form.get('alert_type', 'email')
    target = request.form.get('alert_target', '').strip()

    if not target:
        flash('Alert target is required.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    rule = AlertRule(
        resource_id=resource_id,
        alert_type=alert_type,
        target=target,
        created_by=current_user.id,
    )
    db.session.add(rule)
    AuditLog.log('alert.create', 'alert', None, {'resource_id': resource_id, 'type': alert_type}, user_id=current_user.id)
    db.session.commit()
    flash(f'Alert rule added ({alert_type}: {target}).', 'success')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/alerts/<int:rule_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_alert(resource_id, rule_id):
    rule = db.session.get(AlertRule, rule_id) or abort(404)
    if rule.resource_id != resource_id:
        abort(404)
    db.session.delete(rule)
    db.session.commit()
    flash('Alert rule removed.', 'info')
    return redirect(url_for('resources.detail', resource_id=resource_id))


# ===== Tags Management =====
@bp.route('/tags/manage', methods=['POST'])
@login_required
@admin_required
def manage_tags():
    """Create a new tag."""
    name = request.form.get('tag_name', '').strip()
    color = request.form.get('tag_color', '#6c757d').strip()
    if not name:
        flash('Tag name is required.', 'danger')
        return redirect(request.referrer or url_for('resources.list_resources'))
    existing = Tag.query.filter_by(name=name).first()
    if existing:
        existing.color = color
    else:
        db.session.add(Tag(name=name, color=color))
    db.session.commit()
    flash(f'Tag "{name}" saved.', 'success')
    return redirect(request.referrer or url_for('resources.list_resources'))


@bp.route('/tags/<int:tag_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_tag(tag_id):
    tag = db.session.get(Tag, tag_id) or abort(404)
    db.session.delete(tag)
    db.session.commit()
    flash(f'Tag deleted.', 'info')
    return redirect(request.referrer or url_for('resources.list_resources'))


# ===== Shared Resource Assignments =====
@bp.route('/<int:resource_id>/assign', methods=['POST'])
@login_required
@admin_required
def assign_shared_child(resource_id):
    """Assign a shared child resource to this testbed with a slot count."""
    resource = db.session.get(Resource, resource_id) or abort(404)
    if not resource.is_testbed:
        abort(400)
    child_id = request.form.get('child_id', type=int)
    slots = request.form.get('slots', 1, type=int)
    notes = request.form.get('assignment_notes', '').strip()

    if not child_id or child_id == resource_id:
        flash('Invalid resource selected.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    child = db.session.get(Resource, child_id)
    if not child:
        flash('Resource not found.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    existing = ResourceAssignment.query.filter_by(parent_id=resource_id, child_id=child_id).first()
    if existing:
        flash(f'"{child.name}" is already assigned to this testbed.', 'warning')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    slots = max(1, min(slots, 100))
    assignment = ResourceAssignment(
        parent_id=resource_id,
        child_id=child_id,
        slots=slots,
        notes=notes,
    )
    db.session.add(assignment)
    AuditLog.log('resource.assign', 'resource', resource_id,
                 {'child_id': child_id, 'child_name': child.name, 'slots': slots},
                 user_id=current_user.id)
    db.session.commit()
    flash(f'"{child.name}" assigned as shared resource with {slots} slot(s).', 'success')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/assign/<int:assignment_id>/update', methods=['POST'])
@login_required
@admin_required
def update_assignment(resource_id, assignment_id):
    """Update the slot count on a shared resource assignment."""
    assignment = db.session.get(ResourceAssignment, assignment_id) or abort(404)
    if assignment.parent_id != resource_id:
        abort(404)
    slots = request.form.get('slots', 1, type=int)
    slots = max(1, min(slots, 100))
    assignment.slots = slots
    assignment.notes = request.form.get('assignment_notes', assignment.notes).strip()
    db.session.commit()
    flash(f'Slot count updated to {slots}.', 'success')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/assign/<int:assignment_id>/delete', methods=['POST'])
@login_required
@admin_required
def unassign_shared_child(resource_id, assignment_id):
    """Remove a shared child resource assignment."""
    assignment = db.session.get(ResourceAssignment, assignment_id) or abort(404)
    if assignment.parent_id != resource_id:
        abort(404)
    child_name = assignment.child.name
    AuditLog.log('resource.unassign', 'resource', resource_id,
                 {'child_id': assignment.child_id, 'child_name': child_name},
                 user_id=current_user.id)
    db.session.delete(assignment)
    db.session.commit()
    flash(f'"{child_name}" unassigned from this testbed.', 'info')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/assign-parent', methods=['POST'])
@login_required
@admin_required
def assign_shared_parent(resource_id):
    """Assign a parent testbed to this resource (from the child's view)."""
    resource = db.session.get(Resource, resource_id) or abort(404)
    parent_id = request.form.get('parent_id', type=int)
    slots = request.form.get('slots', 1, type=int)
    notes = request.form.get('assignment_notes', '').strip()

    if not parent_id or parent_id == resource_id:
        flash('Invalid testbed selected.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    parent = db.session.get(Resource, parent_id)
    if not parent:
        flash('Testbed not found.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    existing = ResourceAssignment.query.filter_by(parent_id=parent_id, child_id=resource_id).first()
    if existing:
        flash(f'Already assigned to "{parent.name}".', 'warning')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    slots = max(1, min(slots, 100))
    assignment = ResourceAssignment(
        parent_id=parent_id,
        child_id=resource_id,
        slots=slots,
        notes=notes,
    )
    db.session.add(assignment)
    AuditLog.log('resource.assign_parent', 'resource', resource_id,
                 {'parent_id': parent_id, 'parent_name': parent.name, 'slots': slots},
                 user_id=current_user.id)
    db.session.commit()
    flash(f'Assigned to "{parent.name}" with {slots} slot(s).', 'success')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/unassign-parent/<int:assignment_id>', methods=['POST'])
@login_required
@admin_required
def unassign_shared_parent(resource_id, assignment_id):
    """Remove a parent assignment from the child's view."""
    assignment = db.session.get(ResourceAssignment, assignment_id) or abort(404)
    if assignment.child_id != resource_id:
        abort(404)
    parent_name = assignment.parent.name
    AuditLog.log('resource.unassign_parent', 'resource', resource_id,
                 {'parent_id': assignment.parent_id, 'parent_name': parent_name},
                 user_id=current_user.id)
    db.session.delete(assignment)
    db.session.commit()
    flash(f'Unassigned from "{parent_name}".', 'info')
    return redirect(url_for('resources.detail', resource_id=resource_id))


# ===== Access Points =====
def _find_testbed_for_resource(resource):
    """Return the testbed resource_id to check bookings against."""
    if resource.is_testbed:
        return resource.id
    if resource.parent_id:
        return resource.parent_id
    # Check shared parents
    a = resource.shared_parent_assignments.first()
    if a:
        return a.parent_id
    return resource.id


def _can_access_check(resource):
    """Return True if current user can access this resource's access points."""
    if current_user.is_admin:
        return True
    testbed_id = _find_testbed_for_resource(resource)
    return Booking.user_has_active_booking(current_user.id, testbed_id)


@bp.route('/<int:resource_id>/access-points/add', methods=['POST'])
@login_required
@admin_required
def add_access_point(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    protocol = request.form.get('protocol', 'rdp').strip().lower()
    if protocol not in ('rdp', 'ssh'):
        flash('Protocol must be rdp or ssh.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    # Resolve hostname: from a linked host or a custom value
    host_id = request.form.get('host_id', '').strip()
    custom_hostname = request.form.get('hostname', '').strip()
    if host_id and host_id != 'custom':
        host = db.session.get(ResourceHost, int(host_id))
        if host and host.resource_id == resource_id:
            hostname = host.address
        else:
            flash('Invalid host selected.', 'danger')
            return redirect(url_for('resources.detail', resource_id=resource_id))
    elif custom_hostname:
        hostname = custom_hostname
    else:
        flash('Hostname is required.', 'danger')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    port_str = request.form.get('port', '').strip()
    port = int(port_str) if port_str else None
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    display_name = request.form.get('display_name', '').strip()

    ap = AccessPoint(
        resource_id=resource_id,
        protocol=protocol,
        hostname=hostname,
        port=port,
        username=username,
        display_name=display_name,
    )
    ap.password = password
    db.session.add(ap)
    AuditLog.log('access_point.create', 'access_point', None,
                 {'resource_id': resource_id, 'protocol': protocol, 'hostname': hostname},
                 user_id=current_user.id)
    db.session.commit()
    flash(f'Access point "{ap.label}" added.', 'success')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/access-points/<int:ap_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_access_point(resource_id, ap_id):
    ap = db.session.get(AccessPoint, ap_id) or abort(404)
    if ap.resource_id != resource_id:
        abort(404)
    # Toggle-only form (enable/disable button)
    if 'is_enabled' in request.form and 'protocol' not in request.form:
        ap.is_enabled = request.form.get('is_enabled') == 'true'
        db.session.commit()
        flash(f'Access point {"enabled" if ap.is_enabled else "disabled"}.', 'success')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    ap.protocol = request.form.get('protocol', ap.protocol).strip().lower()
    host_id = request.form.get('host_id', '').strip()
    custom_hostname = request.form.get('hostname', '').strip()
    if host_id and host_id != 'custom':
        host = db.session.get(ResourceHost, int(host_id))
        if host and host.resource_id == resource_id:
            ap.hostname = host.address
    elif custom_hostname:
        ap.hostname = custom_hostname
    port_str = request.form.get('port', '').strip()
    ap.port = int(port_str) if port_str else None
    ap.username = request.form.get('username', ap.username).strip()
    new_password = request.form.get('password', '').strip()
    if new_password:
        ap.password = new_password
    ap.display_name = request.form.get('display_name', ap.display_name).strip()
    ap.is_enabled = 'is_enabled' in request.form
    db.session.commit()
    flash(f'Access point updated.', 'success')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/access-points/<int:ap_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_access_point(resource_id, ap_id):
    ap = db.session.get(AccessPoint, ap_id) or abort(404)
    if ap.resource_id != resource_id:
        abort(404)
    label = ap.label
    AuditLog.log('access_point.delete', 'access_point', ap_id,
                 {'resource_id': resource_id, 'label': label},
                 user_id=current_user.id)
    db.session.delete(ap)
    db.session.commit()
    flash(f'Access point "{label}" removed.', 'info')
    return redirect(url_for('resources.detail', resource_id=resource_id))


@bp.route('/<int:resource_id>/access-points/<int:ap_id>/connect', methods=['POST'])
@login_required
def connect_access_point(resource_id, ap_id):
    ap = db.session.get(AccessPoint, ap_id) or abort(404)
    resource = db.session.get(Resource, ap.resource_id) or abort(404)

    if not _can_access_check(resource):
        flash('You need an active booking to connect. Book this testbed first.', 'warning')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    # Update tracking
    ap.last_accessed_by = current_user.id
    ap.last_accessed_at = datetime.now(timezone.utc)
    AuditLog.log('access.connect', 'access_point', ap_id,
                 {'resource_id': resource_id, 'protocol': ap.protocol},
                 user_id=current_user.id)
    db.session.commit()

    if ap.protocol == 'rdp':
        return jsonify({
            'protocol': 'rdp',
            'rdp_download': url_for('resources.download_rdp_file', resource_id=resource_id, ap_id=ap_id),
        })
    else:
        # SSH: return command, password only for admins
        result = {
            'protocol': 'ssh',
            'command': ap.generate_ssh_command(),
            'hostname': ap.hostname,
            'port': ap.effective_port,
            'username': ap.username,
        }
        if current_user.is_admin:
            result['password'] = ap.password
        return jsonify(result)


@bp.route('/<int:resource_id>/access-points/<int:ap_id>/rdp-file')
@login_required
def download_rdp_file(resource_id, ap_id):
    """Download .rdp file with embedded credentials."""
    ap = db.session.get(AccessPoint, ap_id) or abort(404)
    resource = db.session.get(Resource, ap.resource_id) or abort(404)
    if ap.protocol != 'rdp':
        abort(404)
    # Allow access from the AP's own resource or its parent resource
    if ap.resource_id != resource_id and resource.parent_id != resource_id:
        abort(404)
    if not _can_access_check(resource):
        abort(403)
    rdp_content = ap.generate_rdp_file()
    filename = f'{ap.hostname.replace(".", "_")}_{ap.effective_port}.rdp'
    return Response(
        rdp_content,
        mimetype='application/x-rdp',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@bp.route('/<int:resource_id>/access-points/<int:ap_id>/force-connect', methods=['POST'])
@login_required
def force_connect_access_point(resource_id, ap_id):
    ap = db.session.get(AccessPoint, ap_id) or abort(404)
    resource = db.session.get(Resource, ap.resource_id) or abort(404)

    if not _can_access_check(resource):
        flash('You need an active booking to connect.', 'warning')
        return redirect(url_for('resources.detail', resource_id=resource_id))

    # Notify displaced user
    displaced_user = None
    if ap.last_accessed_by and ap.last_accessed_by != current_user.id:
        from app.models import User
        displaced_user = db.session.get(User, ap.last_accessed_by)
        if displaced_user:
            try:
                from app.email_service import send_force_disconnect_notification
                send_force_disconnect_notification(displaced_user, ap, current_user)
            except Exception:
                pass
            AuditLog.log('access.force_connect', 'access_point', ap_id,
                         {'resource_id': resource_id, 'displaced_user_id': displaced_user.id,
                          'displaced_user': displaced_user.display_name},
                         user_id=current_user.id)

    # Update tracking
    ap.last_accessed_by = current_user.id
    ap.last_accessed_at = datetime.now(timezone.utc)
    if not displaced_user:
        AuditLog.log('access.connect', 'access_point', ap_id,
                     {'resource_id': resource_id, 'protocol': ap.protocol},
                     user_id=current_user.id)
    db.session.commit()

    if ap.protocol == 'rdp':
        return jsonify({
            'protocol': 'rdp',
            'rdp_download': url_for('resources.download_rdp_file', resource_id=resource_id, ap_id=ap_id),
        })
    else:
        result = {
            'protocol': 'ssh',
            'command': ap.generate_ssh_command(),
            'hostname': ap.hostname,
            'port': ap.effective_port,
            'username': ap.username,
        }
        if current_user.is_admin:
            result['password'] = ap.password
        return jsonify(result)


@bp.route('/<int:resource_id>/access-points/<int:ap_id>/password', methods=['POST'])
@login_required
def get_access_point_password(resource_id, ap_id):
    """Return the password — admin only."""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    ap = db.session.get(AccessPoint, ap_id) or abort(404)
    resource = db.session.get(Resource, ap.resource_id) or abort(404)
    if not _can_access_check(resource):
        return jsonify({'error': 'No active booking'}), 403
    return jsonify({'password': ap.password})
