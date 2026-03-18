from flask import render_template, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user

from app.network import bp
from app.network.forms import VlanForm, SubnetForm
from app.extensions import db
from app.models import Vlan, Subnet, ResourceHost, AppSettings


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
    last_sync = AppSettings.get('switch_last_sync', '')
    return render_template('network/overview.html', vlans=vlans, unlinked_hosts=unlinked_hosts,
                           switch_configured=is_switch_configured(), last_sync=last_sync)


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
    ResourceHost.query.update({'subnet_id': None})
    db.session.flush()
    count = 0
    for subnet in Subnet.query.all():
        count += _auto_link_hosts_to_subnet(subnet)
    db.session.commit()
    flash(f'Re-linked {count} hosts to their subnets.', 'success')
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


def _auto_link_hosts_to_subnet(subnet):
    """Link all unlinked hosts whose IP falls in this subnet. Returns count.

    Resolves hostnames via DNS so hosts added by name also get linked.
    """
    import ipaddress as _ipaddress
    from app.models import _resolve_to_ip
    network = _ipaddress.ip_network(subnet.cidr, strict=False)
    count = 0
    for host in ResourceHost.query.filter_by(subnet_id=None).all():
        ip = _resolve_to_ip(host.address)
        if ip and ip in network:
            host.subnet_id = subnet.id
            count += 1
    return count
