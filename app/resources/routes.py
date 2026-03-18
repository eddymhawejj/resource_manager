import re

from flask import render_template, redirect, url_for, flash, abort, request
from flask_login import login_required, current_user

from app.resources import bp
from app.resources.forms import ResourceForm, ChildResourceForm, HostForm
from app.extensions import db
from app.models import Resource, ResourceHost


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
    testbeds = Resource.query.filter_by(parent_id=None).filter(
        Resource.resource_type != 'device'
    ).order_by(Resource.name).all()
    return render_template('resources/list.html', testbeds=testbeds)


@bp.route('/<int:resource_id>')
@login_required
def detail(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    children = Resource.query.filter_by(parent_id=resource_id).order_by(Resource.name).all()
    host_form = HostForm()
    return render_template('resources/detail.html', resource=resource, children=children,
                           host_form=host_form)


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
        db.session.commit()
        flash(f'Testbed "{resource.name}" created.', 'success')
        return redirect(url_for('resources.detail', resource_id=resource.id))
    return render_template('resources/form.html', form=form, title='Add Testbed')


@bp.route('/<int:resource_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_resource(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    form = ResourceForm(obj=resource)
    if form.validate_on_submit():
        form.populate_obj(resource)
        _sync_hosts_from_form(resource)
        db.session.commit()
        flash(f'Resource "{resource.name}" updated.', 'success')
        return redirect(url_for('resources.detail', resource_id=resource.id))
    return render_template('resources/form.html', form=form, title=f'Edit {resource.name}',
                           resource=resource)


@bp.route('/<int:resource_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_resource(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    parent_id = resource.parent_id
    name = resource.name

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
