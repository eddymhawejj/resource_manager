from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, SelectField, BooleanField, PasswordField, IntegerField
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange, EqualTo, ValidationError

from app.models import User


class UserCreateForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    display_name = StringField('Display Name', validators=[DataRequired(), Length(min=2, max=120)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    password2 = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match.')])
    role = SelectField('Role', choices=[('user', 'User'), ('admin', 'Admin')])
    is_active = BooleanField('Active', default=True)

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError('Username already taken.')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('Email already registered.')


class UserEditForm(FlaskForm):
    display_name = StringField('Display Name', validators=[DataRequired(), Length(max=120)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    role = SelectField('Role', choices=[('user', 'User'), ('admin', 'Admin')])
    is_active = BooleanField('Active')


class UserResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    password2 = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match.')])


class SmtpSettingsForm(FlaskForm):
    smtp_host = StringField('SMTP Host', validators=[Optional(), Length(max=200)])
    smtp_port = IntegerField('SMTP Port', validators=[Optional(), NumberRange(1, 65535)], default=587)
    smtp_use_tls = BooleanField('Use TLS', default=True)
    smtp_username = StringField('Username', validators=[Optional(), Length(max=200)])
    smtp_password = PasswordField('Password', validators=[Optional(), Length(max=200)])
    smtp_sender = StringField('Sender Email', validators=[Optional(), Email()])


class LdapSettingsForm(FlaskForm):
    ldap_enabled = BooleanField('Enable LDAP')
    ldap_url = StringField('LDAP URL', validators=[Optional(), Length(max=200)])
    ldap_base_dn = StringField('Base DN', validators=[Optional(), Length(max=200)])
    ldap_user_dn = StringField('User DN', validators=[Optional(), Length(max=200)])
    ldap_bind_dn = StringField('Bind DN', validators=[Optional(), Length(max=200)])
    ldap_bind_password = PasswordField('Bind Password', validators=[Optional(), Length(max=200)])


class LogoUploadForm(FlaskForm):
    logo = FileField('Logo Image', validators=[
        FileAllowed(['png', 'jpg', 'jpeg', 'svg', 'gif'], 'Images only!')
    ])
    app_name = StringField('Application Name', validators=[Optional(), Length(max=100)])


class SwitchSettingsForm(FlaskForm):
    switch_host = StringField('Switch Host/IP', validators=[Optional(), Length(max=200)])
    switch_username = StringField('Username', validators=[Optional(), Length(max=100)])
    switch_password = PasswordField('Password', validators=[Optional(), Length(max=200)])
    switch_use_ssl = BooleanField('Use HTTPS', default=False)
    switch_verify_ssl = BooleanField('Verify SSL Certificate', default=False)
    switch_api_version = SelectField('REST API Version', choices=[
        ('v1', 'v1'), ('v2', 'v2'), ('v3', 'v3'),
        ('v4', 'v4'), ('v5', 'v5'), ('v6', 'v6'), ('v7', 'v7'),
    ], default='v3')
