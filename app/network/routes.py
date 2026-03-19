import ipaddress as _ipaddress

from flask import render_template, redirect, url_for, flash, abort, jsonify, request
from flask_login import login_required, current_user

from app.network import bp
from app.network.forms import VlanForm, SubnetForm
from app.extensions import db
from app.models import Vlan, Subnet, Resource, ResourceHost, AppSettings, PingResult


def _wipe_discovered_devices(subnet_id=None):
    """Delete all resources with resource_type='device'.

    If subnet_id is given, only delete devices whose hosts are in that subnet.
    Returns the count of deleted devices.
    """
    query = Resource.query.filter_by(resource_type='device')
    if subnet_id:
        device_ids_in_subnet = db.session.query(ResourceHost.resource_id).filter_by(
            subnet_id=subnet_id
        ).subquery()
        query = query.filter(Resource.id.in_(db.session.query(device_ids_in_subnet)))

    devices = query.all()
    count = len(devices)
    for device in devices:
        # Delete ping results for device hosts
        host_ids = [h.id for h in device.hosts.all()]
        if host_ids:
            PingResult.query.filter(PingResult.host_id.in_(host_ids)).delete(synchronize_session=False)
        db.session.delete(device)
    if count:
        db.session.commit()
    return count


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
def overview():
    """Network overview: all VLANs with their subnets and linked hosts."""
    from app.network.switch_sync import is_switch_configured
    vlans = Vlan.query.order_by(Vlan.number).all()
    unlinked_hosts = ResourceHost.query.filter_by(subnet_id=None).all()
    discovered_devices = Resource.query.filter_by(resource_type='device').order_by(Resource.name).all()
    last_sync = AppSettings.get('switch_last_sync', '')
    last_discovery = AppSettings.get('switch_last_discovery', '')
    last_scan = AppSettings.get('subnet_last_scan', '')
    return render_template('network/overview.html', vlans=vlans, unlinked_hosts=unlinked_hosts,
                           discovered_devices=discovered_devices,
                           switch_configured=is_switch_configured(),
                           last_sync=last_sync, last_discovery=last_discovery,
                           last_scan=last_scan)


@bp.route('/vlans/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_vlan():
    form = VlanForm()
    if form.validate_on_submit():
        if Vlan.query.filter_by(number=form.number.data).first():
            flash(f'VLAN {form.number.data} already exists.', 'danger')
            return render_template('network/vlan_form.html', form=form, title='Add VLAN')
        vlan = Vlan(
            number=form.number.data,
            name=form.name.data,
            description=form.description.data or '',
        )
        db.session.add(vlan)
        db.session.commit()
        flash(f'VLAN {vlan.number} ({vlan.name}) created.', 'success')
        return redirect(url_for('network.overview'))
    return render_template('network/vlan_form.html', form=form, title='Add VLAN')


@bp.route('/vlans/<int:vlan_id>')
@login_required
def vlan_detail(vlan_id):
    vlan = db.session.get(Vlan, vlan_id) or abort(404)
    return render_template('network/vlan_detail.html', vlan=vlan)


@bp.route('/vlans/<int:vlan_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_vlan(vlan_id):
    vlan = db.session.get(Vlan, vlan_id) or abort(404)
    form = VlanForm(obj=vlan)
    if form.validate_on_submit():
        existing = Vlan.query.filter_by(number=form.number.data).first()
        if existing and existing.id != vlan.id:
            flash(f'VLAN {form.number.data} already exists.', 'danger')
            return render_template('network/vlan_form.html', form=form, title=f'Edit VLAN {vlan.number}', vlan=vlan)
        vlan.number = form.number.data
        vlan.name = form.name.data
        vlan.description = form.description.data or ''
        db.session.commit()
        flash(f'VLAN {vlan.number} updated.', 'success')
        return redirect(url_for('network.vlan_detail', vlan_id=vlan.id))
    return render_template('network/vlan_form.html', form=form, title=f'Edit VLAN {vlan.number}', vlan=vlan)


@bp.route('/vlans/<int:vlan_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_vlan(vlan_id):
    vlan = db.session.get(Vlan, vlan_id) or abort(404)
    # Unlink any hosts in this VLAN's subnets
    for subnet in vlan.subnets.all():
        ResourceHost.query.filter_by(subnet_id=subnet.id).update({'subnet_id': None})
    name = f'VLAN {vlan.number}'
    db.session.delete(vlan)
    db.session.commit()
    flash(f'{name} deleted.', 'success')
    return redirect(url_for('network.overview'))


@bp.route('/subnets/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_subnet():
    form = SubnetForm()
    form.vlan_id.choices = [(v.id, f'VLAN {v.number} - {v.name}') for v in Vlan.query.order_by(Vlan.number).all()]
    if form.validate_on_submit():
        if Subnet.query.filter_by(cidr=form.cidr.data.strip()).first():
            flash(f'Subnet {form.cidr.data} already exists.', 'danger')
            return render_template('network/subnet_form.html', form=form, title='Add Subnet')
        subnet = Subnet(
            vlan_id=form.vlan_id.data,
            cidr=form.cidr.data.strip(),
            name=form.name.data or '',
            gateway=form.gateway.data.strip() if form.gateway.data else None,
            description=form.description.data or '',
        )
        db.session.add(subnet)
        db.session.flush()
        # Auto-link existing unlinked hosts that fall within this subnet
        _auto_link_hosts_to_subnet(subnet)
        db.session.commit()
        flash(f'Subnet {subnet.cidr} created. {subnet.host_count} hosts auto-linked.', 'success')
        return redirect(url_for('network.vlan_detail', vlan_id=subnet.vlan_id))
    return render_template('network/subnet_form.html', form=form, title='Add Subnet')


@bp.route('/subnets/<int:subnet_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_subnet(subnet_id):
    subnet = db.session.get(Subnet, subnet_id) or abort(404)
    form = SubnetForm(obj=subnet)
    form.vlan_id.choices = [(v.id, f'VLAN {v.number} - {v.name}') for v in Vlan.query.order_by(Vlan.number).all()]
    if form.validate_on_submit():
        existing = Subnet.query.filter_by(cidr=form.cidr.data.strip()).first()
        if existing and existing.id != subnet.id:
            flash(f'Subnet {form.cidr.data} already exists.', 'danger')
            return render_template('network/subnet_form.html', form=form, title=f'Edit Subnet', subnet=subnet)
        subnet.vlan_id = form.vlan_id.data
        subnet.cidr = form.cidr.data.strip()
        subnet.name = form.name.data or ''
        subnet.gateway = form.gateway.data.strip() if form.gateway.data else None
        subnet.description = form.description.data or ''
        # Re-link hosts: unlink old, auto-link new
        ResourceHost.query.filter_by(subnet_id=subnet.id).update({'subnet_id': None})
        db.session.flush()
        _auto_link_hosts_to_subnet(subnet)
        db.session.commit()
        flash(f'Subnet {subnet.cidr} updated.', 'success')
        return redirect(url_for('network.vlan_detail', vlan_id=subnet.vlan_id))
    return render_template('network/subnet_form.html', form=form, title=f'Edit Subnet', subnet=subnet)


@bp.route('/subnets/<int:subnet_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_subnet(subnet_id):
    subnet = db.session.get(Subnet, subnet_id) or abort(404)
    vlan_id = subnet.vlan_id
    cidr = subnet.cidr
    ResourceHost.query.filter_by(subnet_id=subnet.id).update({'subnet_id': None})
    db.session.delete(subnet)
    db.session.commit()
    flash(f'Subnet {cidr} deleted.', 'success')
    return redirect(url_for('network.vlan_detail', vlan_id=vlan_id))


@bp.route('/relink', methods=['POST'])
@login_required
@admin_required
def relink_all():
    """Re-run auto-linking for all hosts against all subnets."""
    import ipaddress as _ipaddress
    from app.models import _resolve_to_ip

    # 1. Read all data we need (quick read transaction)
    hosts = [(h.id, h.address) for h in ResourceHost.query.all()]
    subnets = [(s.id, _ipaddress.ip_network(s.cidr, strict=False)) for s in Subnet.query.all()]
    db.session.expire_all()  # release any implicit locks

    # 2. Resolve DNS and match subnets in memory (slow but no DB lock)
    assignments = {}  # host_id -> subnet_id
    for host_id, address in hosts:
        ip = _resolve_to_ip(address)
        if ip:
            for subnet_id, network in subnets:
                if ip in network:
                    assignments[host_id] = subnet_id
                    break

    # 3. Apply all changes in one short transaction
    ResourceHost.query.update({'subnet_id': None})
    for host_id, subnet_id in assignments.items():
        ResourceHost.query.filter_by(id=host_id).update({'subnet_id': subnet_id})
    db.session.commit()

    flash(f'Re-linked {len(assignments)} hosts to their subnets.', 'success')
    return redirect(url_for('network.overview'))


@bp.route('/sync', methods=['POST'])
@login_required
@admin_required
def sync_now():
    """Trigger an on-demand VLAN sync from the switch."""
    from app.network.switch_sync import sync_vlans_from_switch
    result = sync_vlans_from_switch()
    if 'error' in result:
        flash(f'Switch sync failed: {result["error"]}', 'danger')
    else:
        flash(
            f'Switch sync complete: {result["vlans_created"]} VLANs created, '
            f'{result["vlans_updated"]} updated, {result["subnets_created"]} subnets created '
            f'(total {result["total_switch_vlans"]} VLANs on switch).',
            'success'
        )
    return redirect(url_for('network.overview'))


@bp.route('/discover', methods=['POST'])
@login_required
@admin_required
def discover_now():
    """Trigger LLDP-based host discovery from the switch."""
    from app.network.switch_sync import discover_hosts_from_switch

    if request.form.get('wipe_devices'):
        count = _wipe_discovered_devices()
        if count:
            flash(f'Wiped {count} previously discovered device(s).', 'info')

    result = discover_hosts_from_switch()
    if 'error' in result:
        flash(f'Discovery failed: {result["error"]}', 'danger')
    else:
        flash(
            f'Discovery complete: {result["resources_created"]} resources created, '
            f'{result["resources_skipped"]} already existed, '
            f'{result["resources_linked"]} linked to subnets '
            f'({result["devices_discovered"]} LLDP devices found on switch).',
            'success'
        )
    return redirect(url_for('network.overview'))


@bp.route('/scan', methods=['POST'])
@login_required
@admin_required
def scan_subnets():
    """Launch a background ping-sweep scan of all subnets."""
    from flask import current_app
    from app.network.subnet_scan import start_scan_background

    if request.form.get('wipe_devices'):
        count = _wipe_discovered_devices()
        if count:
            flash(f'Wiped {count} previously discovered device(s).', 'info')

    started = start_scan_background(current_app._get_current_object())
    if not started:
        flash('A subnet scan is already running.', 'warning')
    else:
        flash('Subnet scan started. Progress is shown below.', 'info')
    return redirect(url_for('network.overview'))


@bp.route('/subnets/<int:subnet_id>/scan', methods=['POST'])
@login_required
@admin_required
def scan_single_subnet(subnet_id):
    """Launch a background ping-sweep scan of a specific subnet."""
    from flask import current_app
    from app.network.subnet_scan import start_scan_background
    subnet = db.session.get(Subnet, subnet_id) or abort(404)

    if request.form.get('wipe_devices'):
        count = _wipe_discovered_devices(subnet_id=subnet_id)
        if count:
            flash(f'Wiped {count} previously discovered device(s) in {subnet.cidr}.', 'info')

    started = start_scan_background(
        current_app._get_current_object(), subnet_id=subnet_id,
    )
    if not started:
        flash('A subnet scan is already running.', 'warning')
    else:
        flash(f'Scanning {subnet.cidr} in background.', 'info')
    next_url = request.form.get('next') or request.referrer or url_for('network.overview')
    return redirect(next_url)


@bp.route('/scan/progress')
@login_required
@admin_required
def scan_progress():
    """HTMX endpoint: returns scan progress partial HTML."""
    from app.network.subnet_scan import get_scan_progress
    progress = get_scan_progress()

    if not progress['running'] and progress['phase'] == '':
        # No scan has been run
        return ''

    if progress['phase'] == 'done':
        result = progress.get('result', {})
        if 'error' in result:
            return render_template('network/_scan_progress.html', progress=progress, error=result['error'])
        return render_template('network/_scan_progress.html', progress=progress, result=result)

    if progress['phase'] == 'error':
        result = progress.get('result', {})
        return render_template('network/_scan_progress.html', progress=progress, error=result.get('error', 'Unknown error'))

    return render_template('network/_scan_progress.html', progress=progress)


@bp.route('/devices/<int:resource_id>/promote', methods=['POST'])
@login_required
@admin_required
def promote_device(resource_id):
    """Promote a discovered device to a bookable resource (testbed or child)."""
    device = db.session.get(Resource, resource_id) or abort(404)
    if device.resource_type != 'device':
        abort(400)

    resource_type = request.form.get('resource_type', 'testbed')
    if resource_type not in ('testbed', 'server', 'switch', 'other'):
        resource_type = 'testbed'

    parent_id = request.form.get('parent_id', type=int)
    device.resource_type = resource_type

    if parent_id:
        parent = db.session.get(Resource, parent_id)
        if parent:
            device.parent_id = parent_id

    db.session.commit()

    if parent_id and parent:
        flash(f'{device.name} promoted to {resource_type} under {parent.name}.', 'success')
    else:
        flash(f'{device.name} promoted to {resource_type}.', 'success')

    redirect_to = request.form.get('redirect_to')
    if redirect_to == 'detail':
        return redirect(url_for('resources.detail', resource_id=resource_id))
    return redirect(url_for('network.overview'))


@bp.route('/devices/<int:resource_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_device(resource_id):
    """Delete a discovered device."""
    device = db.session.get(Resource, resource_id) or abort(404)
    if device.resource_type != 'device':
        abort(400)
    name = device.name
    db.session.delete(device)
    db.session.commit()
    flash(f'Discovered device {name} deleted.', 'success')
    return redirect(url_for('network.overview'))


@bp.route('/ipam')
@login_required
def ipam():
    """IP Address Management: show used/free IPs per subnet."""
    subnets = Subnet.query.order_by(Subnet.cidr).all()
    subnet_data = []

    for subnet in subnets:
        try:
            network = _ipaddress.ip_network(subnet.cidr, strict=False)
        except ValueError:
            continue

        total_hosts = network.num_addresses - 2  # exclude network + broadcast
        if total_hosts <= 0:
            continue

        used_ips = set()
        hosts_in_subnet = ResourceHost.query.filter_by(subnet_id=subnet.id).all()
        for h in hosts_in_subnet:
            try:
                used_ips.add(str(_ipaddress.ip_address(h.address)))
            except ValueError:
                from app.models import _resolve_to_ip
                ip = _resolve_to_ip(h.address)
                if ip:
                    used_ips.add(str(ip))

        free_count = total_hosts - len(used_ips)
        utilization = (len(used_ips) / total_hosts * 100) if total_hosts > 0 else 0

        subnet_data.append({
            'subnet': subnet,
            'total_hosts': total_hosts,
            'used_count': len(used_ips),
            'free_count': max(free_count, 0),
            'utilization': round(utilization, 1),
            'hosts': hosts_in_subnet,
        })

    return render_template('network/ipam.html', subnet_data=subnet_data)


@bp.route('/topology')
@login_required
def topology():
    """Network topology map: visual diagram of VLANs, subnets, hosts."""
    vlans = Vlan.query.order_by(Vlan.number).all()
    return render_template('network/topology.html', vlans=vlans)


@bp.route('/topology/data')
@login_required
def topology_data():
    """JSON data for the topology diagram."""
    vlans = Vlan.query.order_by(Vlan.number).all()
    nodes = []
    edges = []

    for vlan in vlans:
        vlan_node_id = f'vlan-{vlan.id}'
        nodes.append({
            'id': vlan_node_id,
            'label': f'VLAN {vlan.number}\n{vlan.name}',
            'type': 'vlan',
            'color': '#0d6efd',
        })

        for subnet in vlan.subnets.all():
            subnet_node_id = f'subnet-{subnet.id}'
            nodes.append({
                'id': subnet_node_id,
                'label': f'{subnet.cidr}\n{subnet.name}' if subnet.name else subnet.cidr,
                'type': 'subnet',
                'color': '#198754',
            })
            edges.append({'from': vlan_node_id, 'to': subnet_node_id})

            for host in subnet.hosts.all():
                host_node_id = f'host-{host.id}'
                ping = host.latest_ping
                status_color = '#6c757d'
                if ping:
                    status_color = '#198754' if ping.is_reachable else '#dc3545'
                nodes.append({
                    'id': host_node_id,
                    'label': f'{host.address}\n{host.label}' if host.label else host.address,
                    'type': 'host',
                    'color': status_color,
                    'resource': host.resource.name if host.resource else '',
                })
                edges.append({'from': subnet_node_id, 'to': host_node_id})

    return jsonify({'nodes': nodes, 'edges': edges})


def _auto_link_hosts_to_subnet(subnet):
    """Link all unlinked hosts whose IP falls in this subnet. Returns count.

    Resolves hostnames via DNS so hosts added by name also get linked.
    DNS resolution is done upfront to avoid holding a DB transaction open.
    """
    import ipaddress as _ipaddress
    from app.models import _resolve_to_ip
    network = _ipaddress.ip_network(subnet.cidr, strict=False)

    # Snapshot unlinked hosts, then resolve DNS outside the ORM session
    unlinked = [(h.id, h.address) for h in ResourceHost.query.filter_by(subnet_id=None).all()]

    count = 0
    for host_id, address in unlinked:
        ip = _resolve_to_ip(address)
        if ip and ip in network:
            ResourceHost.query.filter_by(id=host_id).update({'subnet_id': subnet.id})
            count += 1
    return count
