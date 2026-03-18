from datetime import datetime, timedelta

from flask import render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_required, current_user

from app.bookings import bp
from app.bookings.forms import BookingForm
from app.extensions import db
from app.models import Resource, Booking
from app.email_service import send_booking_confirmation, send_booking_cancellation


@bp.route('/')
@login_required
def list_bookings():
    if current_user.is_admin:
        bookings = Booking.query.order_by(Booking.start_time.desc()).all()
    else:
        bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.start_time.desc()).all()
    return render_template('bookings/list.html', bookings=bookings)


@bp.route('/calendar')
@login_required
def calendar():
    testbeds = Resource.query.filter_by(parent_id=None).filter(
        Resource.resource_type != 'device'
    ).order_by(Resource.name).all()
    return render_template('bookings/calendar.html', testbeds=testbeds)


@bp.route('/events')
@login_required
def events():
    """JSON endpoint for FullCalendar."""
    start = request.args.get('start', '')
    end = request.args.get('end', '')
    resource_id = request.args.get('resource_id', type=int)

    query = Booking.query.filter_by(status='confirmed')

    if resource_id:
        query = query.filter_by(resource_id=resource_id)
    if start:
        query = query.filter(Booking.end_time >= start)
    if end:
        query = query.filter(Booking.start_time <= end)

    bookings = query.all()

    colors = ['#0d6efd', '#198754', '#dc3545', '#ffc107', '#0dcaf0', '#6f42c1', '#fd7e14', '#20c997']

    events = []
    for b in bookings:
        # Detect all-day bookings (midnight to midnight)
        is_all_day = (b.start_time.hour == 0 and b.start_time.minute == 0
                      and b.end_time.hour == 0 and b.end_time.minute == 0)
        event = {
            'id': b.id,
            'title': f'{b.title} ({b.resource.name})',
            'start': b.start_time.isoformat(),
            'end': b.end_time.isoformat(),
            'color': colors[b.resource_id % len(colors)],
            'allDay': is_all_day,
            'extendedProps': {
                'user': b.user.display_name,
                'resource': b.resource.name,
                'notes': b.notes or '',
                'bookingId': b.id,
            },
        }
        events.append(event)
    return jsonify(events)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    form = BookingForm()
    testbeds = Resource.query.filter_by(parent_id=None, is_active=True).filter(
        Resource.resource_type != 'device'
    ).order_by(Resource.name).all()
    form.resource_id.choices = [(t.id, t.name) for t in testbeds]

    if form.validate_on_submit():
        if form.all_day.data:
            if not form.all_day_date.data:
                flash('Please select a date for the all-day booking.', 'danger')
                return render_template('bookings/form.html', form=form, title='Create Booking')
            start_time = datetime.combine(form.all_day_date.data, datetime.min.time())
            end_time = start_time + timedelta(days=1)
        else:
            if not form.start_time.data or not form.end_time.data:
                flash('Please provide start and end times.', 'danger')
                return render_template('bookings/form.html', form=form, title='Create Booking')
            start_time = form.start_time.data
            end_time = form.end_time.data

        if start_time >= end_time:
            flash('End time must be after start time.', 'danger')
            return render_template('bookings/form.html', form=form, title='Create Booking')

        booking = Booking(
            resource_id=form.resource_id.data,
            user_id=current_user.id,
            title=form.title.data,
            start_time=start_time,
            end_time=end_time,
            notes=form.notes.data,
            status='confirmed',
        )

        if booking.has_conflict():
            flash('This time slot conflicts with an existing booking.', 'danger')
            return render_template('bookings/form.html', form=form, title='Create Booking')

        db.session.add(booking)
        db.session.commit()

        send_booking_confirmation(booking)
        flash('Booking created successfully.', 'success')
        return redirect(url_for('bookings.list_bookings'))

    return render_template('bookings/form.html', form=form, title='Create Booking')


@bp.route('/<int:booking_id>/cancel', methods=['POST'])
@login_required
def cancel(booking_id):
    booking = db.session.get(Booking, booking_id) or abort(404)
    if booking.user_id != current_user.id and not current_user.is_admin:
        abort(403)

    booking.status = 'cancelled'
    db.session.commit()

    send_booking_cancellation(booking)
    flash('Booking cancelled.', 'info')
    return redirect(url_for('bookings.list_bookings'))
