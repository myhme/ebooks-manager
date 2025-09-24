// static/js/app.js

document.addEventListener('DOMContentLoaded', function(){
  // --- Toast system ---
  const toastContainer = document.createElement('div');
  toastContainer.id = 'toast-container';
  document.body.appendChild(toastContainer);

  function showToast(msg, type="info", timeout=4000){
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.innerText = msg;
    toastContainer.appendChild(t);
    setTimeout(()=>t.classList.add('visible'), 50);
    setTimeout(()=>{
      t.classList.remove('visible');
      setTimeout(()=>toastContainer.removeChild(t), 300);
    }, timeout);
  }
  window.showToast = showToast;

  // --- WebSocket Centralized ---
  const ws = new WebSocket(`ws://${location.host}/ws/updates`);
  const handlers = {};

  ws.onopen = () => {
    console.log("WS connected");
    showToast("✅ Connected to updates", "success", 2000);
    document.querySelectorAll('#wsStatus').forEach(el=>el.innerText="Connected");
  };
  ws.onclose = () => {
    console.log("WS closed");
    showToast("⚠️ Lost connection to updates", "danger", 4000);
    document.querySelectorAll('#wsStatus').forEach(el=>el.innerText="Disconnected");
  };

  ws.onmessage = ev => {
    try {
      const msg = JSON.parse(ev.data);
      console.log("WS event:", msg);
      if(msg.event && handlers[msg.event]){
        handlers[msg.event].forEach(fn => fn(msg));
      }
      // auto-toast some events
      if(msg.event === "queued"){
        showToast(`📥 Queued candidate ${msg.candidate_id || ''}`, "info");
      }
      if(msg.event === "sync_done"){
        showToast(`🔄 Sync finished for shelf ${msg.shelf} (${msg.count || 'unknown'} books)`, "success");
      }
    } catch(err){
      console.error("WS parse error", err, ev.data);
    }
  };

  function registerHandler(event, fn){
    if(!handlers[event]) handlers[event] = [];
    handlers[event].push(fn);
  }
  window.wsHandlers = { register: registerHandler };

  // --- Shared refresh helpers ---
  async function refreshQueue(queueElId){
    try {
      const res = await fetch('/api/status_proxy');
      const j = await res.json();
      document.getElementById(queueElId).innerText = JSON.stringify(j, null, 2);
    } catch(e){
      console.error("Queue refresh failed", e);
    }
  }

  async function refreshActive(tableSelector){
    try {
      const activeRes = await fetch('/api/active_proxy');
      const active = await activeRes.json();
      const tbody = document.querySelector(tableSelector + ' tbody');
      tbody.innerHTML = '';
      if(Array.isArray(active.active_downloads)){
        active.active_downloads.forEach(item=>{
          const progress = item.progress || 0;
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td>${item.title||item.id||'unknown'}</td>
            <td>${item.status||''}</td>
            <td>
              <div class="progress-bar"><div class="progress" style="width:${progress}%;"></div></div>
              ${progress}%
            </td>
            <td>${item.priority||0}</td>
            <td><button class="btn danger" onclick="cancelDownload('${item.id||item.book_id||item.md5}')">Cancel</button></td>
          `;
          tbody.appendChild(tr);
        });
      } else {
        tbody.innerHTML = `<tr><td colspan="5">${JSON.stringify(active)}</td></tr>`;
      }
    } catch(e){
      console.error("Active refresh failed", e);
    }
  }

  window.refreshUI = { refreshQueue, refreshActive };

  // --- Queue button (manual) ---
  document.querySelectorAll('.queue-btn').forEach(btn=>{
    btn.addEventListener('click', async function(e){
      const tr = e.target.closest('tr');
      if(!tr) return;
      const id = tr.dataset.goodreadsId;
      try {
        const res = await fetch('/api/download_proxy', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({id: id, priority: 0})
        });
        const j = await res.json();
        if (res.ok) showToast('Queued: ' + (j.status || JSON.stringify(j)), "success");
        else showToast('Queue error: ' + (j.error || JSON.stringify(j)), "danger");
      } catch(err){
        showToast('Network error: ' + err, "danger");
      }
    });
  });

  // --- Auto-queue button ---
  document.querySelectorAll('.auto-queue-btn').forEach(btn=>{
    btn.addEventListener('click', async function(e){
      const tr = e.target.closest('tr');
      if(!tr) return;
      const payload = {
        goodreads_id: tr.dataset.goodreadsId,
        title: tr.dataset.title,
        author: tr.dataset.author,
        isbn: tr.dataset.isbn || '',
        isbn13: tr.dataset.isbn13 || ''
      };
      e.target.disabled = true;
      e.target.innerText = 'Searching...';
      try {
        const res = await fetch('/api/search_and_queue', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const j = await res.json();
        if(res.ok){
          if(j.queued){
            showToast('✅ Auto-queued: ' + (j.candidate && (j.candidate.title || j.candidate.id) || 'ok'), "success");
          } else if(j.candidates){
            const choose = confirm('No confident match. Queue top candidate?');
            if(choose && j.candidates.length){
              const c = j.candidates[0];
              const qres = await fetch('/api/queue_from_candidate', {
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({candidate_id: c.id})
              });
              const qj = await qres.json();
              showToast('Queued: ' + JSON.stringify(qj), "info");
            }
          } else {
            showToast('Search completed: ' + JSON.stringify(j), "info");
          }
        } else {
          showToast('Search error: ' + JSON.stringify(j), "danger");
        }
      } catch (err){
        showToast('Network error: ' + err, "danger");
      } finally {
        e.target.disabled = false;
        e.target.innerText = 'Auto Queue';
      }
    });
  });

  // --- Per-page select ---
  const pp = document.getElementById('per_page_select');
  if(pp){
    pp.addEventListener('change', function(){
      const params = new URLSearchParams(window.location.search);
      params.set('per_page', this.value);
      params.set('page', '1');
      window.location.search = params.toString();
    });
  }

  // --- Sync button (shelf view) ---
  const syncBtn = document.getElementById('syncBtn');
  if(syncBtn){
    syncBtn.addEventListener('click', async function(ev){
      ev.preventDefault();
      const shelf = (window.location.pathname.split('/').pop() || 'to-download');
      const res = await fetch('/sync/' + encodeURIComponent(shelf), {method:'POST'});
      const j = await res.json();
      document.getElementById('syncStatus').innerText = j.status || JSON.stringify(j);
    });
    window.wsHandlers.register('sync_done', msg=>{
      document.getElementById('syncStatus').innerText = '✅ Sync finished for ' + msg.shelf;
    });
  }

  // --- Downloads page ---
  const refreshBtn = document.getElementById('refreshBtn');
  if(refreshBtn){
    async function refreshDownloads(){
      await refreshQueue('queueStatus');
      await refreshActive('#active-table');
    }
    refreshBtn.addEventListener('click', refreshDownloads);
    window.wsHandlers.register('queued', refreshDownloads);
    window.wsHandlers.register('sync_done', refreshDownloads);
  }

  // --- Status page ---
  const refreshStatusBtn = document.getElementById('refreshStatusBtn');
  if(refreshStatusBtn){
    async function refreshStatus(){
      await refreshQueue('queueData');
      await refreshActive('#status-active-table');
    }
    refreshStatusBtn.addEventListener('click', refreshStatus);
    window.wsHandlers.register('queued', refreshStatus);
    window.wsHandlers.register('sync_done', refreshStatus);
  }

  // --- Manual Search page ---
  const searchForm = document.getElementById('manualSearchForm');
  if(searchForm){
    searchForm.addEventListener('submit', async (ev)=>{
      ev.preventDefault();
      const query = document.getElementById('searchQuery').value.trim();
      if(!query) return;
      const resultBox = document.getElementById('manualSearchResult');
      resultBox.innerText = 'Searching...';
      try {
        const res = await fetch('/api/search_proxy?query=' + encodeURIComponent(query));
        const j = await res.json();
        if(res.ok && Array.isArray(j)){
          let html = '<h3>Results</h3><table class="books-table"><thead><tr><th>Title</th><th>Author</th><th>Action</th></tr></thead><tbody>';
          j.forEach(c=>{
            const id = c.id || c.md5 || '';
            html += `<tr>
              <td>${c.title||''}</td>
              <td>${c.author||''}</td>
              <td><button class="btn queue-result-btn" data-id="${id}">Queue</button></td>
            </tr>`;
          });
          html += '</tbody></table>';
          resultBox.innerHTML = html;
          document.querySelectorAll('.queue-result-btn').forEach(btn=>{
            btn.addEventListener('click', async ()=>{
              const candId = btn.dataset.id;
              const qres = await fetch('/api/queue_from_candidate', {
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({candidate_id: candId})
              });
              const qj = await qres.json();
              showToast('Queued: ' + JSON.stringify(qj), "success");
            });
          });
        } else {
          resultBox.innerText = JSON.stringify(j);
        }
      } catch(err){
        resultBox.innerText = 'Error: ' + err;
      }
    });
  }
});

// --- Cancel helper ---
async function cancelDownload(bookId){
  if(!bookId) return showToast('Missing book id', "danger");
  const res = await fetch('/api/download/' + encodeURIComponent(bookId) + '/cancel', {method: 'DELETE'});
  const j = await res.json();
  showToast(JSON.stringify(j), res.ok ? "success" : "danger");
}
