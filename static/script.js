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

// ── Notifications ─────────────────────────────────────────────────────────────
async function toggleNotifs() {
  const dd = document.getElementById('notif-dropdown');
  const badge = document.getElementById('notif-badge');
  
  if (dd.classList.contains('show')) {
    dd.classList.remove('show');
    return;
  }
  
  // Load
  const r = await fetch('/api/notifications');
  if (r.status === 401) { window.location = '/login'; return; }
  const data = await r.json();
  
  if (!data.length) {
    dd.innerHTML = '<div class="notif-empty">No notifications</div>';
  } else {
    dd.innerHTML = data.map(n => `
      <a href="${n.link}" class="notif-item ${n.is_read ? '' : 'unread'}">
        <div>${n.message}</div>
        <span class="notif-time">${new Date(n.created + 'Z').toLocaleDateString()}</span>
      </a>
    `).join('');
  }
  
  dd.classList.add('show');
  
  // Mark read
  if (data.some(n => !n.is_read)) {
    await fetch('/api/notifications/read', {method:'POST'});
    badge.style.display = 'none';
  }
}

// Check for unread on load
document.addEventListener('DOMContentLoaded', async () => {
  const badge = document.getElementById('notif-badge');
  if (!badge) return; // Not logged in
  const r = await fetch('/api/notifications');
  if (r.ok) {
    const data = await r.json();
    if (data.some(n => !n.is_read)) badge.style.display = 'block';
  }
});

// ── Theme ──────────────────────────────────────────────────────────────────────
function toggleTheme() {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const newTheme = isDark ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
}

// Apply on load
document.addEventListener('DOMContentLoaded', () => {
    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
});

// ── Community Posts ───────────────────────────────────────────────────────────
function togglePostType(radio) {
  // Update UI tabs
  document.querySelectorAll('.post-type-btn').forEach(btn => btn.classList.remove('active'));
  radio.closest('.post-type-btn').classList.add('active');

  const pollFields = document.getElementById('poll-fields');
  const imgSection = document.getElementById('image-upload-section');
  
  if (radio.value === 'poll') {
    pollFields.style.display = 'block';
    imgSection.style.display = 'none';
  } else if (radio.value === 'image') {
    pollFields.style.display = 'none';
    imgSection.style.display = 'block';
  } else {
    pollFields.style.display = 'none';
    imgSection.style.display = 'none';
  }
}

// ── Video Thumbnail Generator ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const videoInput = document.querySelector('input[name="video"]');
  const thumbInput = document.querySelector('input[name="thumbnail"]');
  
  if (videoInput && thumbInput) {
    // Create container for thumbnails
    const container = document.createElement('div');
    container.className = 'thumbnail-generator';
    // Insert after video input
    videoInput.parentNode.insertBefore(container, videoInput.nextSibling);

    videoInput.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      
      container.innerHTML = '<div style="padding:10px;color:var(--text2);font-size:13px"><div class="spinner" style="width:16px;height:16px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:8px"></div> Generating thumbnails...</div>';
      
      try {
        const frames = await generateVideoFrames(file, 3);
        container.innerHTML = '';
        
        const label = document.createElement('p');
        label.style.fontSize = '13px';
        label.style.color = 'var(--text2)';
        label.style.marginBottom = '8px';
        label.style.marginTop = '12px';
        label.textContent = 'Select a thumbnail or upload your own:';
        container.appendChild(label);

        const grid = document.createElement('div');
        grid.className = 'generated-thumbs-grid';
        
        frames.forEach((blob, idx) => {
          const img = document.createElement('img');
          img.src = URL.createObjectURL(blob);
          img.className = 'generated-thumb';
          img.onclick = () => {
            // Visual selection
            document.querySelectorAll('.generated-thumb').forEach(el => el.classList.remove('selected'));
            img.classList.add('selected');
            
            // Update file input
            const fileObj = new File([blob], "thumbnail.jpg", { type: "image/jpeg" });
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(fileObj);
            thumbInput.files = dataTransfer.files;
          };
          grid.appendChild(img);
        });
        container.appendChild(grid);
      } catch (err) {
        console.error(err);
        container.innerHTML = ''; // Fail silently or show error
      }
    });
  }

  // Logic for EDIT page
  const generateBtn = document.getElementById('generate-thumb-btn');
  if (generateBtn && thumbInput) {
    const videoUrl = generateBtn.dataset.videoUrl;
    const gridContainer = document.getElementById('generated-thumbs-grid-edit');

    generateBtn.addEventListener('click', async () => {
        if (!videoUrl || !gridContainer) return;

        generateBtn.textContent = 'Generating...';
        generateBtn.disabled = true;
        gridContainer.innerHTML = '<div style="padding:10px;color:var(--text2);font-size:13px"><div class="spinner" style="width:16px;height:16px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:8px"></div> Generating thumbnails...</div>';

        try {
            const frames = await generateVideoFrames(videoUrl, 3);
            gridContainer.innerHTML = '';

            frames.forEach((blob) => {
                const img = document.createElement('img');
                img.src = URL.createObjectURL(blob);
                img.className = 'generated-thumb';
                img.onclick = () => {
                    document.querySelectorAll('#generated-thumbs-grid-edit .generated-thumb').forEach(el => el.classList.remove('selected'));
                    img.classList.add('selected');

                    const fileObj = new File([blob], "thumbnail.jpg", { type: "image/jpeg" });
                    const dataTransfer = new DataTransfer();
                    dataTransfer.items.add(fileObj);
                    thumbInput.files = dataTransfer.files;
                };
                gridContainer.appendChild(img);
            });

        } catch (err) {
            console.error(err);
            gridContainer.innerHTML = '<div style="color:var(--red); font-size:13px;">Failed to generate thumbnails.</div>';
        } finally {
            generateBtn.textContent = 'Generate from video';
            generateBtn.disabled = false;
        }
    });
  }
});

async function generateVideoFrames(videoSource, count) {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.muted = true;
    video.playsInline = true;

    let videoUrl;
    if (videoSource instanceof File) {
        videoUrl = URL.createObjectURL(videoSource);
        video.src = videoUrl;
    } else if (typeof videoSource === 'string') {
        video.crossOrigin = 'anonymous'; // Good practice for URLs
        videoUrl = videoSource;
        video.src = videoUrl;
    } else {
        return reject(new Error('Invalid video source'));
    }
    
    video.onloadedmetadata = async () => {
      const duration = video.duration;
      const blobs = [];
      
      for (let i = 0; i < count; i++) {
        const time = Math.random() * (duration - 1); // Random time
        video.currentTime = Math.max(0, Math.min(duration, time));
        
        await new Promise(r => {
          const onSeek = () => { video.removeEventListener('seeked', onSeek); r(); };
          video.addEventListener('seeked', onSeek);
        });
        
        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth; canvas.height = video.videoHeight;
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        blobs.push(await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.8)));
      }

      if (videoSource instanceof File) {
        URL.revokeObjectURL(videoUrl); // Only revoke if it was a blob URL
      }
      resolve(blobs);
    };
    video.onerror = (e) => {
        console.error("Video load error:", e);
        reject(new Error('Failed to load video for frame generation.'));
    };
  });
}

function previewPostImage(input) {
  if (input.files && input.files[0]) {
    const reader = new FileReader();
    reader.onload = function(e) {
      document.getElementById('image-preview-img').src = e.target.result;
      document.getElementById('image-preview-box').style.display = 'inline-block';
      document.querySelector('.image-upload-area').style.display = 'none';
    }
    reader.readAsDataURL(input.files[0]);
  }
}

function clearPostImage() {
  document.getElementById('post-image-input').value = '';
  document.getElementById('image-preview-box').style.display = 'none';
  document.querySelector('.image-upload-area').style.display = 'block';
}

function addPollOption() {
  const container = document.getElementById('poll-options-container');
  const count = container.children.length + 1;
  const div = document.createElement('div');
  div.className = 'poll-option-row';
  div.innerHTML = `
    <input type="text" name="poll_options" class="form-input" placeholder="Option ${count}">
    <button type="button" class="poll-delete-btn" onclick="removePollOption(this)" title="Remove option">✕</button>
  `;
  container.appendChild(div);
}

function removePollOption(btn) {
  const container = document.getElementById('poll-options-container');
  if (container.children.length <= 2) {
    showToast('Poll must have at least 2 options');
    return;
  }
  btn.closest('.poll-option-row').remove();
  // Re-index placeholders
  Array.from(container.children).forEach((row, idx) => {
    row.querySelector('input').placeholder = 'Option ' + (idx + 1);
  });
}

async function votePoll(postId, optionIdx) {
  const r = await fetch('/api/community/vote', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({post_id:postId, option_idx:optionIdx}) });
  if (r.ok) window.location.reload();
}

// ── Delete Post ───────────────────────────────────────────────────────────────
let deletePostId = null;
function openDeletePostModal(id) {
  deletePostId = id;
  document.getElementById('delete-post-modal').style.display = 'flex';
}
function closeDeletePostModal() {
  document.getElementById('delete-post-modal').style.display = 'none';
  deletePostId = null;
}
async function confirmDeletePost() {
  if (!deletePostId) return;
  const r = await fetch(`/api/community/delete/${deletePostId}`, { method:'POST' });
  if (r.ok) {
    window.location.reload();
  } else {
    showToast('Failed to delete post');
    closeDeletePostModal();
  }
}

// ── Post Interactions ─────────────────────────────────────────────────────────
async function ratePost(postId, vote, btn) {
  const r = await fetch(`/api/community/rate/${postId}`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({vote})
  });
  if (r.status === 401) { window.location = '/login'; return; }
  const d = await r.json();
  
  const bar = btn.closest('.post-actions-bar');
  const [upBtn, downBtn] = bar.querySelectorAll('.post-action-btn');
  
  upBtn.classList.toggle('active', d.user_vote === 1);
  downBtn.classList.toggle('active', d.user_vote === -1);
  upBtn.querySelector('.count').textContent = d.likes;
  downBtn.querySelector('.count').textContent = d.dislikes;
}

function togglePostComments(postId) {
  const sec = document.getElementById(`post-comments-${postId}`);
  sec.style.display = sec.style.display === 'none' ? 'block' : 'none';
}

async function submitPostComment(postId) {
  const inp = document.getElementById(`post-input-${postId}`);
  const body = inp.value.trim();
  if (!body) return;
  const r = await fetch(`/api/community/comment/${postId}`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({body}) });
  if (r.ok) window.location.reload();
}

let deletePostCommentId = null;
function openDeletePostCommentModal(commentId) {
  deletePostCommentId = commentId;
  document.getElementById('delete-post-comment-modal').style.display = 'flex';
}
function closeDeletePostCommentModal() {
  document.getElementById('delete-post-comment-modal').style.display = 'none';
  deletePostCommentId = null;
}
async function confirmDeletePostComment() {
  if (!deletePostCommentId) return;
  const r = await fetch(`/api/community/comment-delete/${deletePostCommentId}`, { method:'POST' });
  if (r.ok) {
    document.getElementById(`post-comment-${deletePostCommentId}`).remove();
    closeDeletePostCommentModal();
  } else {
    showToast('Failed to delete comment');
    closeDeletePostCommentModal();
  }
}