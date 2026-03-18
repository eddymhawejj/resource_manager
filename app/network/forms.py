import ipaddress

from flask_wtf import FlaskForm
from wtforms import StringField, IntegerField, TextAreaField, SelectField, ValidationError
from wtforms.validators import DataRequired, Length, Optional, NumberRange


def validate_cidr(form, field):
    """Validate that the value is a valid CIDR notation."""
    if not field.data:
        return
    try:
        ipaddress.ip_network(field.data.strip(), strict=False)
    except ValueError:
        raise ValidationError('Enter a valid CIDR notation (e.g. 192.168.1.0/24).')


def validate_ip(form, field):
    """Validate that the value is a valid IP address."""
    if not field.data:
        return
    try:
        ipaddress.ip_address(field.data.strip())
    except ValueError:
        raise ValidationError('Enter a valid IP address.')


class VlanForm(FlaskForm):
    number = IntegerField('VLAN ID', validators=[DataRequired(), NumberRange(min=1, max=4094)])
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional()])


class SubnetForm(FlaskForm):
    vlan_id = SelectField('VLAN', coerce=int, validators=[DataRequired()])
    cidr = StringField('CIDR', validators=[DataRequired(), Length(max=50), validate_cidr])
    name = StringField('Name', validators=[Optional(), Length(max=100)])
    gateway = StringField('Gateway', validators=[Optional(), Length(max=45), validate_ip])
    description = TextAreaField('Description', validators=[Optional()])
