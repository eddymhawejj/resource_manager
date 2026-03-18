import re

from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, BooleanField, ValidationError
from wtforms.validators import DataRequired, Length, Optional


def validate_host(form, field):
    """Validate that the value is a valid IP address or hostname."""
    if not field.data:
        return
    value = field.data.strip()
    # Allow IPv4
    ipv4 = re.match(r'^(\d{1,3}\.){3}\d{1,3}$', value)
    if ipv4:
        parts = value.split('.')
        if all(0 <= int(p) <= 255 for p in parts):
            return
        raise ValidationError('Invalid IPv4 address.')
    # Allow hostname (RFC 952/1123)
    hostname_re = re.compile(r'^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*$')
    if hostname_re.match(value):
        return
    raise ValidationError('Enter a valid IP address or hostname.')


class ResourceForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional()])
    ip_address = StringField('Host', validators=[Optional(), Length(max=255), validate_host])
    resource_type = SelectField('Type', choices=[
        ('testbed', 'Testbed'),
        ('server', 'Server'),
        ('switch', 'Switch'),
        ('router', 'Router'),
        ('vm', 'Virtual Machine'),
        ('other', 'Other'),
    ])
    location = StringField('Location', validators=[Optional(), Length(max=100)])
    is_active = BooleanField('Active', default=True)


class ChildResourceForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional()])
    ip_address = StringField('Host', validators=[Optional(), Length(max=255), validate_host])
    resource_type = SelectField('Type', choices=[
        ('server', 'Server'),
        ('switch', 'Switch'),
        ('router', 'Router'),
        ('vm', 'Virtual Machine'),
        ('controller', 'Controller'),
        ('sensor', 'Sensor'),
        ('other', 'Other'),
    ])
    location = StringField('Location', validators=[Optional(), Length(max=100)])
    is_active = BooleanField('Active', default=True)
