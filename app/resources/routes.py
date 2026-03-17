from flask import render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app.resources import bp
from app.resources.forms import ResourceForm, ChildResourceForm
from app.extensions import db
from app.models import Resource


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
    testbeds = Resource.query.filter_by(parent_id=None).order_by(Resource.name).all()
    return render_template('resources/list.html', testbeds=testbeds)


@bp.route('/<int:resource_id>')
@login_required
def detail(resource_id):
    resource = db.session.get(Resource, resource_id) or abort(404)
    children = Resource.query.filter_by(parent_id=resource_id).order_by(Resource.name).all()
    return render_template('resources/detail.html', resource=resource, children=children)


@bp.route('/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_resource():
    form = ResourceForm()
    if form.validate_on_submit():
        resource = Resource(
            name=form.name.data,
            description=form.description.data,
            ip_address=form.ip_address.data or None,
            resource_type=form.resource_type.data,
            location=form.location.data,
            is_active=form.is_active.data,
        )
        db.session.add(resource)
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
        resource.ip_address = form.ip_address.data or None
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

    # Delete children first
    Resource.query.filter_by(parent_id=resource_id).delete()
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
            ip_address=form.ip_address.data or None,
            resource_type=form.resource_type.data,
            location=form.location.data,
            is_active=form.is_active.data,
            parent_id=testbed_id,
        )
        db.session.add(child)
        db.session.commit()
        flash(f'Child resource "{child.name}" added to {testbed.name}.', 'success')
        return redirect(url_for('resources.detail', resource_id=testbed_id))
    return render_template('resources/form.html', form=form,
                           title=f'Add Child Resource to {testbed.name}', testbed=testbed)
