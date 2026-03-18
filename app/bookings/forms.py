from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, DateTimeLocalField, BooleanField, DateField
from wtforms.validators import DataRequired, Length, Optional


class BookingForm(FlaskForm):
    resource_id = SelectField('Testbed', coerce=int, validators=[DataRequired()])
    title = StringField('Title', validators=[DataRequired(), Length(max=200)])
    all_day = BooleanField('All Day')
    all_day_date = DateField('Date', validators=[Optional()])
    start_time = DateTimeLocalField('Start Time', format='%Y-%m-%dT%H:%M', validators=[Optional()])
    end_time = DateTimeLocalField('End Time', format='%Y-%m-%dT%H:%M', validators=[Optional()])
    notes = TextAreaField('Notes', validators=[Optional()])
