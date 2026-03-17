from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, BooleanField
from wtforms.validators import DataRequired, Length, Optional, IPAddress


class ResourceForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional()])
    ip_address = StringField('IP Address', validators=[Optional(), IPAddress()])
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
    ip_address = StringField('IP Address', validators=[Optional(), IPAddress()])
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
