// static/js/app.js
document.addEventListener('DOMContentLoaded', function(){
  // wire queue + auto-queue buttons if on a shelf page
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
        if (res.ok) alert('Queued: ' + (j.status || JSON.stringify(j)));
        else alert('Queue error: ' + (j.error || JSON.stringify(j)));
      } catch(err){
        alert('Network error: ' + err);
      }
    });
  });

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
            alert('Auto-queued candidate: ' + (j.candidate && (j.candidate.title || j.candidate.id) || 'ok'));
          } else if(j.candidates){
            // show top candidates and let user choose
            const choose = confirm('No confident match. Show top candidate? (OK = choose top)');
            if(choose && j.candidates && j.candidates.length){
              // call queue_from_candidate for first candidate
              const c = j.candidates[0];
              const qres = await fetch('/api/queue_from_candidate', {
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({candidate_id: c.id})
              });
              const qj = await qres.json();
              if(qres.ok) alert('Queued: ' + JSON.stringify(qj));
              else alert('Queue failed: ' + JSON.stringify(qj));
            }
          } else {
            alert('Search completed: ' + JSON.stringify(j));
          }
        } else {
          alert('Search error: ' + JSON.stringify(j));
        }
      } catch (err){
        alert('Network error: ' + err);
      } finally {
        e.target.disabled = false;
        e.target.innerText = 'Auto Queue';
      }
    });
  });

  // per-page select
  const pp = document.getElementById('per_page_select');
  if(pp){
    pp.addEventListener('change', function(){
      const params = new URLSearchParams(window.location.search);
      params.set('per_page', this.value);
      params.set('page', '1');
      window.location.search = params.toString();
    });
  }

  // sync button
  const syncBtn = document.getElementById('syncBtn');
  if(syncBtn){
    syncBtn.addEventListener('click', async function(){
      const shelf = (window.location.pathname.split('/').pop() || 'to-download');
      const res = await fetch('/sync/' + encodeURIComponent(shelf), {method:'POST'});
      const j = await res.json();
      document.getElementById('syncStatus').innerText = j.status || JSON.stringify(j);
      setTimeout(()=>window.location.reload(), 2000);
    });
  }

  // downloads page refresh
  const refreshBtn = document.getElementById('refreshBtn');
  if(refreshBtn){
    refreshBtn.addEventListener('click', ()=>{
      fetch('/api/status_proxy').then(r=>r.json()).then(j=>{
        document.getElementById('queueStatus').innerText = JSON.stringify(j, null, 2);
      }).catch(e=>document.getElementById('queueStatus').innerText = e);
      fetch('/api/active_proxy').then(r=>r.json()).then(j=>{
        const tbody = document.querySelector('#active-table tbody');
        tbody.innerHTML = '';
        if(Array.isArray(j.active_downloads)){
          j.active_downloads.forEach(item=>{
            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${item.title||item.id||'unknown'}</td><td>${item.status||''}</td>
                            <td>${(item.progress||0)}%</td><td>${item.priority||0}</td>
                            <td><button class="btn" onclick="cancelDownload('${item.id||item.book_id||item.md5}')">Cancel</button></td>`;
            tbody.appendChild(tr);
          });
        } else {
          tbody.innerHTML = `<tr><td colspan="5">${JSON.stringify(j)}</td></tr>`;
        }
      }).catch(e=>console.error(e));
    });
  }

});

// helper used in downloads UI
async function cancelDownload(bookId){
  if(!bookId) return alert('Missing book id');
  const res = await fetch('/api/download/' + encodeURIComponent(bookId) + '/cancel_proxy', {method: 'DELETE'});
  const j = await res.json();
  alert(JSON.stringify(j));
}
