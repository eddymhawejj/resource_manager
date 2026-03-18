import os

from flask import render_template, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.admin import bp
from app.admin.forms import UserCreateForm, UserEditForm, UserResetPasswordForm, SmtpSettingsForm, LdapSettingsForm, LogoUploadForm, SwitchSettingsForm
from app.extensions import db
from app.models import User, Resource, Booking, AppSettings


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
@admin_required
def dashboard():
    stats = {
        'users': User.query.count(),
        'resources': Resource.query.filter_by(parent_id=None).count(),
        'child_resources': Resource.query.filter(Resource.parent_id.isnot(None)).count(),
        'bookings': Booking.query.filter_by(status='confirmed').count(),
    }
    return render_template('admin/dashboard.html', stats=stats)


@bp.route('/users')
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=all_users)


@bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    form = UserEditForm(obj=user)
    if form.validate_on_submit():
        user.display_name = form.display_name.data
        user.email = form.email.data
        user.role = form.role.data
        user.is_active = form.is_active.data
        db.session.commit()
        flash(f'User "{user.username}" updated.', 'success')
        return redirect(url_for('admin.users'))
    return render_template('admin/edit_user.html', form=form, user=user)


@bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash('Cannot deactivate your own account.', 'warning')
        return redirect(url_for('admin.users'))
    user.is_active = not user.is_active
    db.session.commit()
    status = 'activated' if user.is_active else 'deactivated'
    flash(f'User "{user.username}" {status}.', 'success')
    return redirect(url_for('admin.users'))


@bp.route('/users/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    form = UserCreateForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            display_name=form.display_name.data,
            role=form.role.data,
            is_active=form.is_active.data,
            auth_type='local',
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f'User "{user.username}" created.', 'success')
        return redirect(url_for('admin.users'))
    return render_template('admin/create_user.html', form=form)


@bp.route('/users/<int:user_id>/reset-password', methods=['GET', 'POST'])
@login_required
@admin_required
def reset_password(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.auth_type != 'local':
        flash('Cannot reset password for LDAP users.', 'warning')
        return redirect(url_for('admin.users'))
    form = UserResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash(f'Password reset for "{user.username}".', 'success')
        return redirect(url_for('admin.users'))
    return render_template('admin/reset_password.html', form=form, user=user)


@bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash('Cannot delete your own account.', 'warning')
        return redirect(url_for('admin.users'))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{username}" deleted.', 'success')
    return redirect(url_for('admin.users'))


@bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    smtp_form = SmtpSettingsForm(
        smtp_host=AppSettings.get('smtp_host', ''),
        smtp_port=int(AppSettings.get('smtp_port', '587')),
        smtp_use_tls=AppSettings.get('smtp_use_tls', 'true') == 'true',
        smtp_username=AppSettings.get('smtp_username', ''),
        smtp_sender=AppSettings.get('smtp_sender', ''),
    )
    ldap_form = LdapSettingsForm(
        ldap_enabled=AppSettings.get('ldap_enabled', 'false') == 'true',
        ldap_url=AppSettings.get('ldap_url', ''),
        ldap_base_dn=AppSettings.get('ldap_base_dn', ''),
        ldap_user_dn=AppSettings.get('ldap_user_dn', ''),
        ldap_bind_dn=AppSettings.get('ldap_bind_dn', ''),
    )
    logo_form = LogoUploadForm(
        app_name=AppSettings.get('app_name', 'Resource Manager'),
    )

    switch_form = SwitchSettingsForm(
        switch_host=AppSettings.get('switch_host', ''),
        switch_username=AppSettings.get('switch_username', ''),
        switch_use_ssl=AppSettings.get('switch_use_ssl', 'false') == 'true',
        switch_verify_ssl=AppSettings.get('switch_verify_ssl', 'false') == 'true',
        switch_api_version=AppSettings.get('switch_api_version', 'v3'),
    )

    return render_template('admin/settings.html',
                           smtp_form=smtp_form, ldap_form=ldap_form, logo_form=logo_form,
                           switch_form=switch_form)


@bp.route('/settings/smtp', methods=['POST'])
@login_required
@admin_required
def save_smtp():
    form = SmtpSettingsForm()
    if form.validate_on_submit():
        AppSettings.set('smtp_host', form.smtp_host.data or '')
        AppSettings.set('smtp_port', str(form.smtp_port.data or 587))
        AppSettings.set('smtp_use_tls', 'true' if form.smtp_use_tls.data else 'false')
        AppSettings.set('smtp_username', form.smtp_username.data or '')
        if form.smtp_password.data:
            AppSettings.set('smtp_password', form.smtp_password.data)
        AppSettings.set('smtp_sender', form.smtp_sender.data or '')
        flash('SMTP settings saved.', 'success')
    else:
        flash('Invalid SMTP settings.', 'danger')
    return redirect(url_for('admin.settings'))


@bp.route('/settings/ldap', methods=['POST'])
@login_required
@admin_required
def save_ldap():
    form = LdapSettingsForm()
    if form.validate_on_submit():
        AppSettings.set('ldap_enabled', 'true' if form.ldap_enabled.data else 'false')
        AppSettings.set('ldap_url', form.ldap_url.data or '')
        AppSettings.set('ldap_base_dn', form.ldap_base_dn.data or '')
        AppSettings.set('ldap_user_dn', form.ldap_user_dn.data or '')
        AppSettings.set('ldap_bind_dn', form.ldap_bind_dn.data or '')
        if form.ldap_bind_password.data:
            AppSettings.set('ldap_bind_password', form.ldap_bind_password.data)
        flash('LDAP settings saved.', 'success')
    else:
        flash('Invalid LDAP settings.', 'danger')
    return redirect(url_for('admin.settings'))


@bp.route('/settings/branding', methods=['POST'])
@login_required
@admin_required
def save_branding():
    form = LogoUploadForm()
    if form.validate_on_submit():
        if form.app_name.data:
            AppSettings.set('app_name', form.app_name.data)

        if form.logo.data:
            filename = secure_filename(form.logo.data.filename)
            upload_path = os.path.join(current_app.static_folder, 'uploads', filename)
            form.logo.data.save(upload_path)
            AppSettings.set('logo_path', f'uploads/{filename}')
            flash('Logo uploaded.', 'success')
        else:
            flash('Branding settings saved.', 'success')
    else:
        flash('Invalid branding settings.', 'danger')
    return redirect(url_for('admin.settings'))


@bp.route('/settings/switch', methods=['POST'])
@login_required
@admin_required
def save_switch():
    form = SwitchSettingsForm()
    if form.validate_on_submit():
        AppSettings.set('switch_host', form.switch_host.data or '')
        AppSettings.set('switch_username', form.switch_username.data or '')
        if form.switch_password.data:
            AppSettings.set('switch_password', form.switch_password.data)
        AppSettings.set('switch_use_ssl', 'true' if form.switch_use_ssl.data else 'false')
        AppSettings.set('switch_verify_ssl', 'true' if form.switch_verify_ssl.data else 'false')
        AppSettings.set('switch_api_version', form.switch_api_version.data or 'v3')
        flash('Switch settings saved.', 'success')
    else:
        flash('Invalid switch settings.', 'danger')
    return redirect(url_for('admin.settings'))
