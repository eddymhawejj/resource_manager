from datetime import datetime, timedelta

from flask import render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from app.bookings import bp
from app.bookings.forms import BookingForm
from app.extensions import db
from app.models import Resource, Booking, AuditLog, WaitlistEntry, MaintenanceWindow
from app.email_service import send_booking_confirmation, send_booking_cancellation


@bp.route('/')
@login_required
def list_bookings():
    page = request.args.get('page', 1, type=int)
    per_page = 25
    if current_user.is_admin:
        query = Booking.query.order_by(Booking.start_time.desc())
    else:
        query = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.start_time.desc())
    total = query.count()
    bookings = query.options(
        joinedload(Booking.resource), joinedload(Booking.user)
    ).offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page
    return render_template('bookings/list.html', bookings=bookings,
                           page=page, total_pages=total_pages, total=total)


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

    bookings = query.options(
        joinedload(Booking.resource), joinedload(Booking.user)
    ).all()

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

        # Block booking during maintenance windows
        if MaintenanceWindow.resource_in_maintenance(booking.resource_id):
            flash('This resource is currently in a maintenance window and cannot be booked.', 'danger')
            return render_template('bookings/form.html', form=form, title='Create Booking')

        db.session.add(booking)
        AuditLog.log('booking.create', 'booking', None, {
            'title': booking.title,
            'resource_id': booking.resource_id,
            'start': start_time.isoformat(),
            'end': end_time.isoformat(),
        }, user_id=current_user.id)
        db.session.commit()

        send_booking_confirmation(booking)
        try:
            from app.monitoring.alert_service import send_teams_booking_notification
            send_teams_booking_notification(booking, 'created')
        except Exception:
            pass
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
    AuditLog.log('booking.cancel', 'booking', booking.id, {
        'title': booking.title,
        'resource_id': booking.resource_id,
    }, user_id=current_user.id)
    db.session.commit()

    send_booking_cancellation(booking)
    try:
        from app.monitoring.alert_service import send_teams_booking_notification
        send_teams_booking_notification(booking, 'cancelled')
    except Exception:
        pass

    # Notify waitlist entries for this resource/time slot
    _notify_waitlist(booking)

    flash('Booking cancelled.', 'info')
    return redirect(url_for('bookings.list_bookings'))


def _notify_waitlist(cancelled_booking):
    """Notify waitlist entries that overlap with a cancelled booking's time slot."""
    import logging
    logger = logging.getLogger(__name__)

    entries = WaitlistEntry.query.filter(
        WaitlistEntry.resource_id == cancelled_booking.resource_id,
        WaitlistEntry.status == 'waiting',
        WaitlistEntry.desired_start < cancelled_booking.end_time,
        WaitlistEntry.desired_end > cancelled_booking.start_time,
    ).all()

    for entry in entries:
        entry.status = 'notified'
        entry.notified_at = datetime.now()
        # Try to send email notification
        try:
            from app.email_service import _is_smtp_configured, _update_mail_config
            from flask_mail import Message
            from app.extensions import mail
            if _is_smtp_configured():
                _update_mail_config()
                msg = Message(
                    subject=f'Waitlist: {cancelled_booking.resource.name} may be available',
                    recipients=[entry.user.email],
                )
                msg.body = (
                    f'A booking for {cancelled_booking.resource.name} has been cancelled.\n\n'
                    f'The time slot {cancelled_booking.start_time.strftime("%Y-%m-%d %H:%M")} - '
                    f'{cancelled_booking.end_time.strftime("%Y-%m-%d %H:%M")} may now be available.\n\n'
                    f'Book it before someone else does!'
                )
                mail.send(msg)
        except Exception as e:
            logger.error(f'Failed to notify waitlist entry {entry.id}: {e}')

    db.session.commit()


@bp.route('/waitlist/add', methods=['POST'])
@login_required
def add_to_waitlist():
    resource_id = request.form.get('resource_id', type=int)
    desired_start = request.form.get('desired_start', '')
    desired_end = request.form.get('desired_end', '')
    notes = request.form.get('notes', '')

    if not resource_id or not desired_start or not desired_end:
        flash('Please fill in all required fields.', 'danger')
        return redirect(url_for('bookings.calendar'))

    try:
        start_dt = datetime.fromisoformat(desired_start)
        end_dt = datetime.fromisoformat(desired_end)
    except ValueError:
        flash('Invalid date format.', 'danger')
        return redirect(url_for('bookings.calendar'))

    entry = WaitlistEntry(
        resource_id=resource_id,
        user_id=current_user.id,
        desired_start=start_dt,
        desired_end=end_dt,
        notes=notes,
    )
    db.session.add(entry)
    AuditLog.log('waitlist.add', 'waitlist', None, {
        'resource_id': resource_id,
    }, user_id=current_user.id)
    db.session.commit()
    flash('Added to waitlist. You will be notified if the slot becomes available.', 'success')
    return redirect(url_for('bookings.list_bookings'))


@bp.route('/waitlist')
@login_required
def waitlist():
    query = WaitlistEntry.query.options(
        joinedload(WaitlistEntry.user), joinedload(WaitlistEntry.resource)
    ).order_by(WaitlistEntry.created_at.desc())
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)
    entries = query.all()
    return render_template('bookings/waitlist.html', entries=entries)


@bp.route('/waitlist/<int:entry_id>/remove', methods=['POST'])
@login_required
def remove_from_waitlist(entry_id):
    entry = db.session.get(WaitlistEntry, entry_id) or abort(404)
    if entry.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(entry)
    db.session.commit()
    flash('Removed from waitlist.', 'info')
    return redirect(url_for('bookings.waitlist'))
