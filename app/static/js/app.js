// ===== Theme Toggle =====
(function () {
  const theme = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', theme);
})();

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);

  const icon = document.getElementById('theme-icon');
  if (icon) {
    icon.className = next === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
  }
}

// ===== Sidebar Toggle (mobile) =====
function toggleSidebar() {
  document.querySelector('.sidebar').classList.toggle('show');
}

// ===== Set theme icon on load =====
document.addEventListener('DOMContentLoaded', function () {
  const theme = document.documentElement.getAttribute('data-theme');
  const icon = document.getElementById('theme-icon');
  if (icon) {
    icon.className = theme === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
  }

  // Auto-dismiss alerts after 5s
  document.querySelectorAll('.alert-dismissible').forEach(function (alert) {
    setTimeout(function () {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
      bsAlert.close();
    }, 5000);
  });
});

// ===== Booking Calendar Init =====
function initCalendar(elementId, eventsUrl) {
  const calendarEl = document.getElementById(elementId);
  if (!calendarEl) return null;

  const calendar = new FullCalendar.Calendar(calendarEl, {
    initialView: 'dayGridMonth',
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,listWeek'
    },
    events: function (info, successCallback, failureCallback) {
      const resourceFilter = document.getElementById('calendar-resource-filter');
      let url = eventsUrl + '?start=' + info.startStr + '&end=' + info.endStr;
      if (resourceFilter && resourceFilter.value) {
        url += '&resource_id=' + resourceFilter.value;
      }
      fetch(url)
        .then(resp => resp.json())
        .then(data => successCallback(data))
        .catch(err => failureCallback(err));
    },
    eventClick: function (info) {
      const props = info.event.extendedProps;
      const modal = document.getElementById('eventDetailModal');
      if (modal) {
        document.getElementById('modal-title').textContent = info.event.title;
        document.getElementById('modal-user').textContent = props.user;
        document.getElementById('modal-resource').textContent = props.resource;
        document.getElementById('modal-start').textContent = info.event.start.toLocaleString();
        document.getElementById('modal-end').textContent = info.event.end ? info.event.end.toLocaleString() : '';
        document.getElementById('modal-notes').textContent = props.notes || 'None';
        new bootstrap.Modal(modal).show();
      }
    },
    height: 'auto',
    nowIndicator: true,
    editable: false,
    selectable: false,
  });

  calendar.render();
  return calendar;
}
