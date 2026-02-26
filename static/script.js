// Sidebar toggle
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  sidebar.classList.toggle('sidebar-open');
}

// Keyboard shortcut: "/" focuses search
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
    e.preventDefault();
    const inp = document.querySelector('.search-input');
    if (inp) inp.focus();
  }
});

// Allow Enter key to post comments
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.activeElement.id === 'comment-input') {
    e.preventDefault();
    const uuid = document.querySelector('[data-vid-uuid]')?.dataset.vidUuid;
    // fallback: parse from URL
    const match = window.location.pathname.match(/\/watch\/([a-z0-9]+)/);
    if (match) postComment(match[1]);
  }
});

// Auto-dismiss alerts
document.querySelectorAll('.alert').forEach(el => {
  setTimeout(() => el.style.opacity = '0', 4000);
  setTimeout(() => el.remove(), 4500);
  el.style.transition = 'opacity .5s';
});