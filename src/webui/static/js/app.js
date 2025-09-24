/*
 static/js/app.js
 Centralizes:
  - WebSocket handling + event dispatch
  - toast notifications
  - partial AJAX refresh (refreshPageSection)
  - bindings for queue/auto-queue buttons and pagination/per-page
  - pushState support for shelf, downloads, status, search
  - real-time download progress updates
*/
(function () {
  // --- Toast system ---
  const toastContainer = document.createElement('div');
  toastContainer.id = 'toast-container';
  document.body.appendChild(toastContainer);
  function showToast(msg, type='info', timeout=4000) {
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.innerText = msg;
    toastContainer.appendChild(t);
    setTimeout(()=>t.classList.add('visible'),50);
    setTimeout(()=>{ t.classList.remove('visible'); setTimeout(()=>toastContainer.removeChild(t),300); }, timeout);
  }
  window.showToast = showToast;

  // --- WebSocket ---
  const wsProto = (location.protocol === 'https:') ? 'wss' : 'ws';
  const ws = new WebSocket(`${wsProto}://${location.host}/ws/updates`);
  const handlers = {};
  ws.onopen = () => { console.log('WS open'); showToast('Connected to updates','success',1500); };
  ws.onclose = () => { console.log('WS closed'); showToast('Lost WS connection','danger',3000); };
  ws.onmessage = ev => {
    try {
      const msg = JSON.parse(ev.data);
      if(msg.event && handlers[msg.event]) handlers[msg.event].forEach(fn=>fn(msg));

      // generic notifications for some events
      if(msg.event === 'queued') showToast('Queued: '+(msg.candidate_id||''),'info');
      if(msg.event === 'sync_done') showToast('Sync finished: '+(msg.shelf||''),'success');
      if(msg.event === 'config_updated') {
        const ta = document.getElementById('configTextarea');
        if(ta) { ta.value = JSON.stringify(msg.config||{}, null, 2); showToast('Config updated (whitelisted)','info'); }
      }

      // 🔥 Real-time progress updates
      if(msg.event === 'download_progress'){
        updateDownloadProgress(msg);
      }
    } catch(e) { console.error('ws parse', e); }
  };
  function registerHandler(ev, fn){ if(!handlers[ev]) handlers[ev]=[]; handlers[ev].push(fn); }
  window.wsHandlers = { register: registerHandler };

  // --- binding helpers ---
  function bindQueueButtons(scope=document) {
    // queue
    scope.querySelectorAll('.queue-btn').forEach(btn=>{
      btn.onclick = async e=>{
        const tr = e.target.closest('tr'); if(!tr) return;
        const id = tr.dataset.goodreadsId;
        try {
          const r = await fetch('/api/download_proxy', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ id, priority:0 }) });
          const j = await r.json();
          showToast('Queued: '+JSON.stringify(j), r.ok ? 'success' : 'danger');
        } catch(err){ showToast('Network error: '+err,'danger'); }
      };
    });
    // auto-queue
    scope.querySelectorAll('.auto-queue-btn').forEach(btn=>{
      btn.onclick = async e=>{
        const tr = e.target.closest('tr'); if(!tr) return;
        const payload = { goodreads_id: tr.dataset.goodreadsId, title: tr.dataset.title, author: tr.dataset.author, isbn: tr.dataset.isbn||'' };
        e.target.disabled = true; e.target.innerText = 'Searching...';
        try {
          const r = await fetch('/api/search_and_queue', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
          const j = await r.json();
          if(r.ok){
            if(j.queued) showToast('Auto queued: '+(j.candidate && (j.candidate.title||j.candidate.id)||''),'success');
            else showToast('Search results: '+JSON.stringify(j),'info');
          } else showToast('Search error: '+JSON.stringify(j),'danger');
        } catch(err){ showToast('Network error: '+err,'danger'); }
        finally { e.target.disabled=false; e.target.innerText='Auto Queue'; }
      };
    });
  }

  window.cancelDownload = async function(bookId){
    if(!bookId) return showToast('Missing id','danger');
    try {
      const r = await fetch('/api/download/'+encodeURIComponent(bookId)+'/cancel', { method:'DELETE' });
      const j = await r.json();
      showToast('Cancel: '+JSON.stringify(j), r.ok ? 'success' : 'danger');
    } catch(e){ showToast('Network error: '+e,'danger'); }
  };

  // --- refreshPageSection(pageType, opts) ---
  // pageType: 'shelf' (opts: shelf,page,per_page), 'downloads', 'status', 'search' (opts: q)
  async function refreshPageSection(pageType, opts={}) {
    try {
      if(pageType === 'shelf') {
        const shelf = opts.shelf || (window.location.pathname.split('/').pop()||'to-download');
        const page = opts.page || (new URLSearchParams(window.location.search).get('page')||1);
        const per = opts.per_page || (new URLSearchParams(window.location.search).get('per_page')||30);
        const res = await fetch(`/shelf/${encodeURIComponent(shelf)}/partial?page=${page}&per_page=${per}`);
        if(!res.ok) throw new Error('partial failed');
        const html = await res.text();
        const doc = new DOMParser().parseFromString(html,'text/html');
        const newTable = doc.querySelector('#shelf-table');
        if(newTable){
          const container = document.querySelector('#shelfTableContainer');
          container.innerHTML = newTable.outerHTML;
          bindQueueButtons(container);
        }
        const newUrl = `/shelf/${encodeURIComponent(shelf)}?page=${page}&per_page=${per}`;
        history.pushState({page:parseInt(page), per_page:parseInt(per)}, '', newUrl);
        showToast('Shelf refreshed', 'info');
        return;
      }

      if(pageType === 'downloads'){
        const qres = await fetch('/downloads/partials/queue');
        if(qres.ok){
          const qhtml = await qres.text();
          const qdoc = new DOMParser().parseFromString(qhtml,'text/html');
          const newQ = qdoc.querySelector('#queuePanelContent');
          const panel = document.querySelector('#queuePanel');
          if(newQ && panel) panel.innerHTML = newQ.innerHTML;
        }
        const tres = await fetch('/downloads/partials/active');
        if(tres.ok){
          const thtml = await tres.text();
          const tdoc = new DOMParser().parseFromString(thtml,'text/html');
          const newTbody = tdoc.querySelector('tbody');
          const tbody = document.querySelector('#active-table tbody');
          if(newTbody && tbody) tbody.innerHTML = newTbody.innerHTML;
        }
        history.pushState({}, '', '/downloads');
        showToast('Downloads refreshed', 'info');
        return;
      }

      if(pageType === 'status'){
        const qres = await fetch('/status/partials/queue');
        if(qres.ok){
          const qhtml = await qres.text();
          const qdoc = new DOMParser().parseFromString(qhtml,'text/html');
          const newQ = qdoc.querySelector('#queuePanelContent');
          const panel = document.querySelector('#queuePanel');
          if(newQ && panel) panel.innerHTML = newQ.innerHTML;
        }
        const tres = await fetch('/downloads/partials/active');
        if(tres.ok){
          const thtml = await tres.text();
          const tdoc = new DOMParser().parseFromString(thtml,'text/html');
          const newTbody = tdoc.querySelector('tbody');
          const tbody = document.querySelector('#status-active-table tbody');
          if(newTbody && tbody) tbody.innerHTML = newTbody.innerHTML;
        }
        history.pushState({}, '', '/status');
        showToast('Status refreshed', 'info');
        return;
      }

      if(pageType === 'search'){
        const q = opts.q || '';
        const res = await fetch(`/manual_search?partial=1&q=${encodeURIComponent(q)}`);
        if(!res.ok) throw new Error('search partial failed');
        const html = await res.text();
        const doc = new DOMParser().parseFromString(html,'text/html');
        const newRes = doc.querySelector('#manualSearchResult');
        const target = document.getElementById('manualSearchResult');
        if(newRes && target) target.innerHTML = newRes.innerHTML;
        history.pushState({q}, '', `/manual_search?q=${encodeURIComponent(q)}`);
        bindQueueButtons(target||document);
        showToast('Search updated', 'info');
        return;
      }
    } catch(e){
      console.error('refreshPageSection error', e);
      showToast('Refresh failed: '+e, 'danger');
    }
  }
  window.refreshPageSection = refreshPageSection;

  // --- pagination / per-page binding (shelf) ---
  function bindPaginationAndPerPage() {
    document.querySelectorAll('.page-link').forEach(a=>{
      a.onclick = async ev=>{
        ev.preventDefault();
        const url = new URL(a.href, window.location.origin);
        const params = url.searchParams;
        const page = params.get('page') || '1';
        const per = params.get('per_page') || (document.getElementById('per_page_select') && document.getElementById('per_page_select').value) || '30';
        await refreshPageSection('shelf', { shelf: window.location.pathname.split('/').pop(), page: page, per_page: per });
      };
    });
    const pp = document.getElementById('per_page_select');
    if(pp){
      pp.onchange = async function(){
        const per = this.value;
        await refreshPageSection('shelf', { shelf: window.location.pathname.split('/').pop(), page: 1, per_page: per });
      };
    }
  }

  // --- 🔥 Update downloads progress in-place ---
  function updateDownloadProgress(msg){
    const { id, progress, status, title } = msg;
    const table = document.querySelector('#active-table tbody') || document.querySelector('#status-active-table tbody');
    if(!table) return;

    let row = table.querySelector(`tr[data-id="${id}"]`);
    if(!row){
      // create new row if not exists
      row = document.createElement('tr');
      row.dataset.id = id;
      row.innerHTML = `
        <td>${title||id}</td>
        <td class="dl-status">${status||''}</td>
        <td class="dl-progress"><div class="progress-bar"><div class="progress" style="width:${progress||0}%"></div></div> ${progress||0}%</td>
        <td>0</td>
        <td><button class="btn danger" onclick="cancelDownload('${id}')">Cancel</button></td>
      `;
      table.appendChild(row);
    } else {
      row.querySelector('.dl-status').innerText = status;
      const pb = row.querySelector('.dl-progress .progress');
      if(pb) pb.style.width = (progress||0) + '%';
      row.querySelector('.dl-progress').lastChild.textContent = ` ${progress||0}%`;
    }
  }

  // --- initial bindings based on page ---
  document.addEventListener('DOMContentLoaded', ()=>{
    bindQueueButtons(document);
    bindPaginationAndPerPage();

    // Sync WS events to refresh pages
    wsHandlers.register('queued', ()=>{ if(location.pathname.startsWith('/downloads')||location.pathname.startsWith('/status')) refreshPageSection(location.pathname.startsWith('/downloads')? 'downloads' : 'status'); });
    wsHandlers.register('sync_done', msg=>{
      const currentShelf = location.pathname.split('/').pop();
      if(location.pathname.startsWith('/shelf') && msg.shelf === currentShelf){
        refreshPageSection('shelf', { shelf: currentShelf, page: new URLSearchParams(window.location.search).get('page')||1, per_page: new URLSearchParams(window.location.search).get('per_page')||30 });
      } else {
        if(location.pathname.startsWith('/downloads')) refreshPageSection('downloads');
        if(location.pathname.startsWith('/status')) refreshPageSection('status');
      }
    });

    const refreshBtn = document.getElementById('refreshBtn');
    if(refreshBtn) refreshBtn.onclick = ()=>refreshPageSection('downloads');
    const refreshStatusBtn = document.getElementById('refreshStatusBtn');
    if(refreshStatusBtn) refreshStatusBtn.onclick = ()=>refreshPageSection('status');

    const sf = document.getElementById('manualSearchForm');
    if(sf){
      sf.onsubmit = async ev=>{
        ev.preventDefault();
        const q = (document.getElementById('searchQuery')||{}).value || '';
        if(!q) return;
        await refreshPageSection('search', { q });
      };
      const uparams = new URLSearchParams(window.location.search);
      const q0 = uparams.get('q');
      if(q0) refreshPageSection('search', { q: q0 });
    }

    const syncBtn = document.getElementById('syncBtn');
    if(syncBtn){
      syncBtn.onclick = async ()=>{
        const shelf = window.location.pathname.split('/').pop();
        try {
          const r = await fetch('/sync/'+encodeURIComponent(shelf), { method:'POST' });
          const j = await r.json();
          const el = document.getElementById('syncStatus'); if(el) el.innerText = j.status || 'started';
        } catch(e){ showToast('Sync error: '+e,'danger'); }
      };
    }

    if(location.pathname.startsWith('/shelf/')){
      const page = parseInt(new URLSearchParams(window.location.search).get('page')||'1');
      const per = parseInt(new URLSearchParams(window.location.search).get('per_page')||'30');
      history.replaceState({page, per_page: per}, '', window.location.href);
    } else {
      history.replaceState({}, '', window.location.href);
    }

    window.addEventListener('popstate', ev=>{
      const path = location.pathname;
      if(path.startsWith('/shelf/')){
        const shelf = path.split('/').pop();
        const page = ev.state && ev.state.page || new URLSearchParams(window.location.search).get('page') || 1;
        const per = ev.state && ev.state.per_page || new URLSearchParams(window.location.search).get('per_page') || 30;
        refreshPageSection('shelf', { shelf, page, per });
      } else if(path === '/downloads') refreshPageSection('downloads');
      else if(path === '/status') refreshPageSection('status');
      else if(path === '/manual_search') {
        const q = (ev.state && ev.state.q) || new URLSearchParams(window.location.search).get('q') || '';
        if(q) refreshPageSection('search', { q });
      }
    });
  });

})();
