from flask import render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user

from app.auth import bp
from app.auth.forms import LoginForm, RegisterForm
from app.auth.ldap_auth import authenticate_ldap, sync_user_groups
from app.extensions import db
from app.models import User, AppSettings


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('resources.list_resources'))

    form = LoginForm()

    ldap_enabled = current_app.config.get('LDAP_ENABLED', False)
    if not ldap_enabled:
        ldap_enabled = AppSettings.get('ldap_enabled', 'false') == 'true'

    if not ldap_enabled:
        form.auth_type.choices = [('local', 'Local')]

    if form.validate_on_submit():
        if form.auth_type.data == 'ldap' and ldap_enabled:
            ldap_config = {
                'LDAP_URL': AppSettings.get('ldap_url', current_app.config.get('LDAP_URL', '')),
                'LDAP_BASE_DN': AppSettings.get('ldap_base_dn', current_app.config.get('LDAP_BASE_DN', '')),
                'LDAP_USER_DN': AppSettings.get('ldap_user_dn', current_app.config.get('LDAP_USER_DN', '')),
            }
            ldap_info = authenticate_ldap(form.username.data, form.password.data, ldap_config)
            if ldap_info:
                user = User.query.filter_by(username=ldap_info['username']).first()
                if not user:
                    user = User(
                        username=ldap_info['username'],
                        email=ldap_info['email'],
                        display_name=ldap_info['display_name'],
                        auth_type='ldap',
                        role='user',
                    )
                    db.session.add(user)
                    db.session.commit()
                # Sync LDAP group memberships to local groups
                if ldap_info.get('groups'):
                    sync_user_groups(user, ldap_info['groups'])
                    db.session.commit()
                login_user(user, remember=form.remember.data)
                next_page = request.args.get('next')
                flash('Logged in successfully.', 'success')
                return redirect(next_page or url_for('resources.list_resources'))
            else:
                flash('Invalid LDAP credentials.', 'danger')
        else:
            user = User.query.filter_by(username=form.username.data).first()
            if user and user.check_password(form.password.data):
                if not user.is_active:
                    flash('Account is deactivated. Contact an administrator.', 'warning')
                    return render_template('auth/login.html', form=form, ldap_enabled=ldap_enabled)
                login_user(user, remember=form.remember.data)
                next_page = request.args.get('next')
                flash('Logged in successfully.', 'success')
                return redirect(next_page or url_for('resources.list_resources'))
            else:
                flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html', form=form, ldap_enabled=ldap_enabled)


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('resources.list_resources'))

    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            display_name=form.display_name.data,
            auth_type='local',
            role='user',
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form=form)


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))
