// ── Sidebar toggle ────────────────────────────────────────────────────────────
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  if (!sidebar) return;

  if (window.innerWidth <= 720) {
    // Mobile: slide in/out
    sidebar.classList.toggle('sidebar-mobile-open');
    overlay.classList.toggle('active');
  } else {
    // Desktop: collapse to icons / expand to full
    document.body.classList.toggle('sidebar-collapsed');
  }
}

// Close sidebar when clicking the overlay (mobile)
document.addEventListener('DOMContentLoaded', () => {
  const overlay = document.getElementById('sidebar-overlay');
  if (overlay) {
    overlay.addEventListener('click', () => {
      document.getElementById('sidebar').classList.remove('sidebar-mobile-open');
      overlay.classList.remove('active');
    });
  }
});

// ── "/" key focuses search ─────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === '/' &&
      document.activeElement.tagName !== 'INPUT' &&
      document.activeElement.tagName !== 'TEXTAREA') {
    e.preventDefault();
    const inp = document.querySelector('.search-input');
    if (inp) inp.focus();
  }
});

// ── Auto-dismiss alerts ───────────────────────────────────────────────────────
document.querySelectorAll('.alert').forEach(el => {
  el.style.transition = 'opacity .5s';
  setTimeout(() => { el.style.opacity = '0'; }, 5000);
  setTimeout(() => { el.remove(); }, 5500);
});

// ── Report modal ──────────────────────────────────────────────────────────────
function openReportModal(type, id) {
  const modal = document.getElementById('report-modal');
  if (!modal) { window.location = '/login'; return; }
  modal.dataset.type = type;
  modal.dataset.id   = id;
  modal.style.display = 'flex';
  // Reset
  document.querySelectorAll('input[name="report-reason-opt"]').forEach(r => r.checked = false);
  document.getElementById('report-reason').value = '';
}

function closeReportModal() {
  const modal = document.getElementById('report-modal');
  if (modal) modal.style.display = 'none';
}

// Close on backdrop click
window.addEventListener('click', e => {
  const modal = document.getElementById('report-modal');
  if (modal && e.target === modal) closeReportModal();
});

async function submitReport() {
  const modal    = document.getElementById('report-modal');
  const selected = document.querySelector('input[name="report-reason-opt"]:checked');
  const extra    = document.getElementById('report-reason').value.trim();
  const reason   = selected ? selected.value + (extra ? ': ' + extra : '') : extra;

  if (!reason) { showToast('Please select a reason.'); return; }

  const body = { reason };
  if (modal.dataset.type === 'video')   body.video_id   = modal.dataset.id;
  if (modal.dataset.type === 'comment') body.comment_id = parseInt(modal.dataset.id);

  const res = await fetch('/api/report', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  closeReportModal();
  if (res.ok) {
    showToast('Report submitted. Our team will review it shortly.');
  } else {
    const d = await res.json().catch(() => ({}));
    showToast(d.error || 'Could not submit report. Please sign in first.');
  }
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg) {
  const t = document.createElement('div');
  t.className   = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('toast-show'));
  setTimeout(() => {
    t.classList.remove('toast-show');
    setTimeout(() => t.remove(), 400);
  }, 3500);
}