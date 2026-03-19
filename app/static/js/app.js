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

  // Show toasts
  document.querySelectorAll('.toast').forEach(function (toastEl) {
    var toast = new bootstrap.Toast(toastEl);
    toast.show();
  });

  // Init global search
  initGlobalSearch();

  // Init sortable tables
  initSortableTables();

  // Init table filters
  initTableFilters();

  // Init back to top
  initBackToTop();

  // Init copy buttons
  initCopyButtons();
});

// ===== Global Search =====
function initGlobalSearch() {
  const input = document.getElementById('global-search');
  const resultsEl = document.getElementById('search-results');
  if (!input || !resultsEl) return;

  let debounceTimer = null;
  let activeIndex = -1;

  // Keyboard shortcut: / to focus search
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && !isInputFocused()) {
      e.preventDefault();
      input.focus();
    }
    // Escape to close
    if (e.key === 'Escape' && document.activeElement === input) {
      input.blur();
      resultsEl.classList.remove('show');
    }
  });

  input.addEventListener('input', function () {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (q.length < 2) {
      resultsEl.classList.remove('show');
      return;
    }
    debounceTimer = setTimeout(function () {
      fetch('/search?q=' + encodeURIComponent(q))
        .then(r => r.json())
        .then(data => renderSearchResults(data, resultsEl));
    }, 200);
  });

  input.addEventListener('keydown', function (e) {
    const items = resultsEl.querySelectorAll('.search-result-item');
    if (!items.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, items.length - 1);
      updateActiveResult(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      updateActiveResult(items);
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      items[activeIndex].click();
    }
  });

  // Close on click outside
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.global-search-wrapper')) {
      resultsEl.classList.remove('show');
    }
  });

  function updateActiveResult(items) {
    items.forEach(function (item, i) {
      item.classList.toggle('active', i === activeIndex);
    });
  }
}

function renderSearchResults(data, container) {
  container.innerHTML = '';
  if (!data.length) {
    container.innerHTML = '<div class="search-no-results">No results found</div>';
    container.classList.add('show');
    return;
  }
  data.forEach(function (item) {
    var a = document.createElement('a');
    a.className = 'search-result-item';
    a.href = item.url;
    a.innerHTML = '<i class="bi ' + item.icon + '"></i>' +
      '<div><div class="search-label">' + escapeHtml(item.label) + '</div>' +
      '<div class="search-detail">' + escapeHtml(item.detail) + '</div></div>';
    container.appendChild(a);
  });
  container.classList.add('show');
}

function isInputFocused() {
  var tag = document.activeElement.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || document.activeElement.isContentEditable;
}

function escapeHtml(str) {
  var div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ===== Sortable Tables =====
function initSortableTables() {
  document.querySelectorAll('table.table').forEach(function (table) {
    var headers = table.querySelectorAll('thead th');
    headers.forEach(function (th, colIndex) {
      // Skip columns with no text or action columns
      var text = th.textContent.trim();
      if (!text || text === 'Actions' || text === '' || th.hasAttribute('data-no-sort')) return;
      th.setAttribute('data-sort', colIndex);
      th.addEventListener('click', function () {
        sortTable(table, colIndex, th);
      });
    });
  });
}

function sortTable(table, colIndex, th) {
  var tbody = table.querySelector('tbody');
  if (!tbody) return;
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var isAsc = th.classList.contains('sort-asc');

  // Clear other sort indicators on this table
  table.querySelectorAll('th[data-sort]').forEach(function (h) {
    h.classList.remove('sort-asc', 'sort-desc');
  });

  var direction = isAsc ? 'desc' : 'asc';
  th.classList.add('sort-' + direction);

  rows.sort(function (a, b) {
    var aVal = getCellValue(a, colIndex);
    var bVal = getCellValue(b, colIndex);

    // Try numeric
    var aNum = parseFloat(aVal.replace(/[^0-9.\-]/g, ''));
    var bNum = parseFloat(bVal.replace(/[^0-9.\-]/g, ''));
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return direction === 'asc' ? aNum - bNum : bNum - aNum;
    }
    // Try date
    var aDate = Date.parse(aVal);
    var bDate = Date.parse(bVal);
    if (!isNaN(aDate) && !isNaN(bDate)) {
      return direction === 'asc' ? aDate - bDate : bDate - aDate;
    }
    // String comparison
    return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
  });

  rows.forEach(function (row) { tbody.appendChild(row); });
}

function getCellValue(row, index) {
  var cell = row.cells[index];
  if (!cell) return '';
  // Use data-value if present
  if (cell.hasAttribute('data-value')) return cell.getAttribute('data-value');
  return cell.textContent.trim();
}

// ===== Table Filters =====
function initTableFilters() {
  document.querySelectorAll('[data-table-filter]').forEach(function (input) {
    var tableId = input.getAttribute('data-table-filter');
    var table = document.getElementById(tableId) || input.closest('.card')?.querySelector('table');
    if (!table) return;
    input.addEventListener('input', function () {
      filterTable(table, input.value);
    });
  });
}

function filterTable(table, query) {
  var tbody = table.querySelector('tbody');
  if (!tbody) return;
  var lowerQ = query.toLowerCase();
  tbody.querySelectorAll('tr').forEach(function (row) {
    var text = row.textContent.toLowerCase();
    row.style.display = text.includes(lowerQ) ? '' : 'none';
  });
}

// ===== Copy to Clipboard =====
function initCopyButtons() {
  document.querySelectorAll('.copy-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var text = btn.getAttribute('data-copy');
      if (!text) {
        var parent = btn.closest('code, .copy-target');
        if (parent) text = parent.textContent.trim();
      }
      if (text) {
        navigator.clipboard.writeText(text).then(function () {
          btn.classList.add('copied');
          var origIcon = btn.innerHTML;
          btn.innerHTML = '<i class="bi bi-check"></i>';
          setTimeout(function () {
            btn.classList.remove('copied');
            btn.innerHTML = origIcon;
          }, 1500);
        });
      }
    });
  });
}

// Utility: attach copy button to code elements matching a selector
function addCopyToElements(selector) {
  document.querySelectorAll(selector).forEach(function (el) {
    if (el.querySelector('.copy-btn')) return;
    var btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.title = 'Copy';
    btn.setAttribute('data-copy', el.textContent.trim());
    btn.innerHTML = '<i class="bi bi-clipboard"></i>';
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      navigator.clipboard.writeText(el.textContent.trim()).then(function () {
        btn.classList.add('copied');
        btn.innerHTML = '<i class="bi bi-check"></i>';
        setTimeout(function () {
          btn.classList.remove('copied');
          btn.innerHTML = '<i class="bi bi-clipboard"></i>';
        }, 1500);
      });
    });
    el.style.position = 'relative';
    el.appendChild(btn);
  });
}

// ===== Back to Top =====
function initBackToTop() {
  var btn = document.getElementById('back-to-top');
  if (!btn) return;
  var content = document.querySelector('.content-area') || window;
  window.addEventListener('scroll', function () {
    if (window.scrollY > 400) {
      btn.classList.add('show');
    } else {
      btn.classList.remove('show');
    }
  });
}

// ===== CSV Export =====
function exportTableToCSV(tableEl, filename) {
  var table = typeof tableEl === 'string' ? document.querySelector(tableEl) : tableEl;
  if (!table) return;
  var rows = [];
  table.querySelectorAll('tr').forEach(function (tr) {
    if (tr.style.display === 'none') return;
    var cols = [];
    tr.querySelectorAll('th, td').forEach(function (cell) {
      var text = cell.textContent.trim().replace(/"/g, '""');
      // Skip action columns
      if (cell.closest('th, td') && cell.closest('th, td').classList.contains('no-export')) return;
      cols.push('"' + text + '"');
    });
    rows.push(cols.join(','));
  });
  var csv = rows.join('\n');
  var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  var link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename || 'export.csv';
  link.click();
  URL.revokeObjectURL(link.href);
}

// ===== Access Point Connect =====
function _handleConnectResponse(data, csrfToken) {
  if (data.protocol === 'rdp') {
    // Auto-download launcher via hidden <a download> click
    var a = document.createElement('a');
    a.href = data.rdp_download;
    a.download = data.rdp_filename || '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  } else {
    // SSH: show modal with command (password only visible to admins)
    document.getElementById('ssh-command').value = data.command || '';
    var pwRow = document.getElementById('ssh-password-row');
    if (data.password) {
      document.getElementById('ssh-password').value = data.password;
      document.getElementById('ssh-password').type = 'password';
      if (pwRow) pwRow.classList.remove('d-none');
    } else {
      document.getElementById('ssh-password').value = '';
      if (pwRow) pwRow.classList.add('d-none');
    }
    new bootstrap.Modal(document.getElementById('sshModal')).show();
  }

  // If user has no booking, offer to quick-book
  if (data.needs_booking && data.testbed_id) {
    document.getElementById('quick-book-testbed-id').value = data.testbed_id;
    setTimeout(function () {
      new bootstrap.Modal(document.getElementById('quickBookModal')).show();
    }, 500);
  }
}

function connectAccess(resourceId, apId, protocol) {
  var csrfToken = document.querySelector('input[name="csrf_token"]')?.value ||
                  document.querySelector('meta[name="csrf-token"]')?.content || '';

  fetch('/resources/' + resourceId + '/access-points/' + apId + '/connect', {
    method: 'POST',
    headers: { 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' }
  })
  .then(function (r) { return r.json(); })
  .then(function (data) { _handleConnectResponse(data, csrfToken); })
  .catch(function (err) {
    showToast('Failed to connect: ' + err, 'danger');
  });
}

function forceConnect(resourceId, apId, protocol, bookerName) {
  // Show confirmation modal instead of browser confirm()
  var modal = document.getElementById('forceConnectModal');
  document.getElementById('force-connect-booker-name').textContent = bookerName;
  var confirmBtn = document.getElementById('force-connect-confirm-btn');
  // Replace old listener by cloning the button
  var newBtn = confirmBtn.cloneNode(true);
  confirmBtn.parentNode.replaceChild(newBtn, confirmBtn);
  newBtn.addEventListener('click', function () {
    var bsModal = bootstrap.Modal.getInstance(modal);
    if (bsModal) bsModal.hide();

    var csrfToken = document.querySelector('input[name="csrf_token"]')?.value ||
                    document.querySelector('meta[name="csrf-token"]')?.content || '';
    fetch('/resources/' + resourceId + '/access-points/' + apId + '/force-connect', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' }
    })
    .then(function (r) { return r.json(); })
    .then(function (data) { _handleConnectResponse(data, csrfToken); })
    .catch(function (err) {
      showToast('Failed to connect: ' + err, 'danger');
    });
  });
  new bootstrap.Modal(modal).show();
}

function copyToClipboard(text, btn) {
  navigator.clipboard.writeText(text).then(function () {
    var origIcon = btn.innerHTML;
    btn.innerHTML = '<i class="bi bi-check"></i>';
    setTimeout(function () { btn.innerHTML = origIcon; }, 1500);
  });
}

function submitQuickBook() {
  var testbedId = document.getElementById('quick-book-testbed-id').value;
  var hours = document.getElementById('quick-book-duration').value;
  var csrfToken = document.querySelector('input[name="csrf_token"]')?.value ||
                  document.querySelector('meta[name="csrf-token"]')?.content || '';

  fetch('/resources/' + testbedId + '/quick-book', {
    method: 'POST',
    headers: { 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' },
    body: JSON.stringify({ hours: parseInt(hours) })
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    var modal = bootstrap.Modal.getInstance(document.getElementById('quickBookModal'));
    if (modal) modal.hide();
    if (data.success) {
      showToast('Booked for ' + hours + ' hour(s).', 'success');
      setTimeout(function () { location.reload(); }, 1000);
    } else {
      showToast(data.error || 'Failed to book.', 'danger');
    }
  })
  .catch(function (err) {
    showToast('Failed to book: ' + err, 'danger');
  });
}

function togglePasswordVisibility(fieldId, btn) {
  var field = document.getElementById(fieldId);
  if (field.type === 'password') {
    field.type = 'text';
    btn.innerHTML = '<i class="bi bi-eye-slash"></i>';
  } else {
    field.type = 'password';
    btn.innerHTML = '<i class="bi bi-eye"></i>';
  }
}

function showToast(message, category, delay, isHtml) {
  var container = document.getElementById('toast-container');
  if (!container) return;
  var icons = { success: 'check-circle', danger: 'exclamation-triangle', warning: 'exclamation-circle', info: 'info-circle' };
  var toast = document.createElement('div');
  toast.className = 'toast align-items-center text-bg-' + (category || 'info') + ' border-0';
  toast.setAttribute('role', 'alert');
  var content = isHtml ? message : escapeHtml(message);
  toast.innerHTML = '<div class="d-flex"><div class="toast-body"><i class="bi bi-' +
    (icons[category] || 'info-circle') + ' me-1"></i>' + content +
    '</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
  container.appendChild(toast);
  new bootstrap.Toast(toast, { delay: delay || 5000 }).show();
  toast.addEventListener('hidden.bs.toast', function () { toast.remove(); });
}

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
