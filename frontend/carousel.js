 (function(){
      const slidesEl = document.getElementById('slides');
      const slides = Array.from(document.querySelectorAll('.slide'));
      const prevBtn = document.getElementById('prev');
      const nextBtn = document.getElementById('next');
      const dotsEl = document.getElementById('dots');

      let idx = 0;
      const total = slides.length;

      for(let i=0;i<total;i++){
        const d = document.createElement('div');
        d.className = 'dot' + (i===0? ' active':'');
        d.dataset.index = i;
        d.addEventListener('click', ()=> goTo(parseInt(d.dataset.index)));
        dotsEl.appendChild(d);
      }

      function update(){
        const w = slidesEl.clientWidth; 
        slidesEl.style.transform = `translateX(-${idx * w}px)`;
 
        document.querySelectorAll('.dot').forEach((d,i)=> d.classList.toggle('active', i===idx));

      }

      function goTo(i){
        idx = (i + total) % total;
        update();
      }

      nextBtn.addEventListener('click', ()=> goTo(idx+1));
      prevBtn.addEventListener('click', ()=> goTo(idx-1));

      let startX = 0;
      let isDragging = false;
      slidesEl.addEventListener('pointerdown', (e)=>{
        isDragging = true;
        startX = e.clientX;
        slidesEl.style.transition = 'none';
      });
      window.addEventListener('pointerup', (e)=>{
        if(!isDragging) return;
        isDragging = false;
        slidesEl.style.transition = '';
        const diff = e.clientX - startX;
        if(diff < -50) goTo(idx+1);
        else if(diff > 50) goTo(idx-1);
        else update();
      });
      window.addEventListener('pointermove', (e)=>{
        if(!isDragging) return;
        const diff = e.clientX - startX;
        const w = slidesEl.clientWidth;
        slidesEl.style.transform = `translateX(${ -idx * w + diff }px)`;
      });

      window.addEventListener('keydown', (e)=>{
        if(e.key === 'ArrowLeft') goTo(idx-1);
        if(e.key === 'ArrowRight') goTo(idx+1);
      });

      let autoplay = true;
      let autoplayMs = 3500;
      let autoplayTimer = null;
      function startAutoplay(){
        if(!autoplay) return;
        stopAutoplay();
        autoplayTimer = setInterval(()=> goTo(idx+1), autoplayMs);
      }
      function stopAutoplay(){ if(autoplayTimer) clearInterval(autoplayTimer); }

      const carousel = document.getElementById('carousel');
      carousel.addEventListener('mouseenter', stopAutoplay);
      carousel.addEventListener('mouseleave', startAutoplay);

      window.addEventListener('resize', update);

      update();
      startAutoplay();
 })();


let users = []; 
let loggedInUser = JSON.parse(localStorage.getItem('loggedInUser')) || null;


document.getElementById('signupForm').addEventListener('submit', function(e){
  e.preventDefault();
  const name = this.querySelector('input[type="text"]').value;
  const email = this.querySelector('input[type="email"]').value;
  const pass = this.querySelector('input[type="password"]').value;

  if(users.some(u => u.email === email)){
    alert('User already exists! Please login.');
    bootstrap.Modal.getInstance(document.getElementById('signupModal')).hide();
    document.querySelector('[data-bs-target="#loginModal"]').click();
    return;
  }

  users.push({name, email, pass});
  alert('Signup successful! You can now login.');
  bootstrap.Modal.getInstance(document.getElementById('signupModal')).hide();
  document.querySelector('[data-bs-target="#loginModal"]').click();
});

document.getElementById('loginForm').addEventListener('submit', function(e){
  e.preventDefault();
  const email = this.querySelector('input[type="email"]').value;
  const pass = this.querySelector('input[type="password"]').value;

  const user = users.find(u => u.email === email && u.pass === pass);
  if(user){
    alert(`Welcome back, ${user.name}!`);
    loggedInUser = user;
    localStorage.setItem('loggedInUser', JSON.stringify(user)); 
    bootstrap.Modal.getInstance(document.getElementById('loginModal')).hide();
    if(window.pendingUpload) {
      handleUpload(window.pendingUpload);
      window.pendingUpload = null;
    }
  } else {
    alert('User not found! Please Sign Up first.');
    bootstrap.Modal.getInstance(document.getElementById('loginModal')).hide();
    document.querySelector('[data-bs-target="#signupModal"]').click();
  }
});

loggedInUser = JSON.parse(localStorage.getItem('loggedInUser')) || null;
const uploadForm = document.getElementById('uploadForm');
const qaSection = document.getElementById('qaSection');

function handleUpload(form){
  alert("Document uploaded successfully!");
  qaSection.style.display = "block";
}

uploadForm.addEventListener('submit', function(e){
  e.preventDefault();

  if(!loggedInUser){
    window.pendingUpload = this;

    const authModal = new bootstrap.Modal(document.getElementById('authChoiceModal'));
    authModal.show();
    return;
  }

  handleUpload(this);
});

document.getElementById('askBtn').addEventListener('click', function(){
  const question = document.getElementById('userQuestion').value;
  if(!question) return;
  document.getElementById('answer').innerHTML = "Answer: Your coverage includes this according to clause 5...";
});


document.getElementById('loginForm').addEventListener('submit', function(e){
  e.preventDefault();
  const email = this.querySelector('input[type="email"]').value;
  const pass = this.querySelector('input[type="password"]').value;

  let users = JSON.parse(localStorage.getItem('users')) || [];
  const user = users.find(u => u.email === email && u.pass === pass);

  if(user){
    alert(`Welcome back, ${user.name}!`);
    loggedInUser = user; 
    localStorage.setItem('loggedInUser', JSON.stringify(user));
    bootstrap.Modal.getInstance(document.getElementById('loginModal')).hide();

    if(window.pendingUpload){
      handleUpload(window.pendingUpload);
      window.pendingUpload = null;
    }
  } else {
    alert("User not found! Please Sign Up first.");
    bootstrap.Modal.getInstance(document.getElementById('loginModal')).hide();
    document.querySelector('[data-bs-target="#signupModal"]').click();
  }
});

document.getElementById('signupForm').addEventListener('submit', function(e){
  e.preventDefault();
  const name = this.querySelector('input[type="text"]').value;
  const email = this.querySelector('input[type="email"]').value;
  const pass = this.querySelector('input[type="password"]').value;

  let users = JSON.parse(localStorage.getItem('users')) || [];
  if(users.some(u => u.email === email)){
    alert('User already exists! Please login.');
    bootstrap.Modal.getInstance(document.getElementById('signupModal')).hide();
    document.querySelector('[data-bs-target="#loginModal"]').click();
    return;
  }

  const newUser = { name, email, pass };
  users.push(newUser);
  localStorage.setItem('users', JSON.stringify(users));
  alert('Signup successful! You can now login.');
  bootstrap.Modal.getInstance(document.getElementById('signupModal')).hide();
  document.querySelector('[data-bs-target="#loginModal"]').click();
});


function typeAnswer(text, targetEl, speed = 50) {
    targetEl.innerHTML = '';
    let i = 0;
    const interval = setInterval(() => {
        targetEl.innerHTML += text.charAt(i);
        i++;
        if (i >= text.length) clearInterval(interval);
    }, speed);
}

document.getElementById('askBtn').addEventListener('click', function(){
    const question = document.getElementById('userQuestion').value;
    if(!question) return;
    
    const answerText = "Answer: Your coverage includes this according to clause 5...";
    const answerEl = document.getElementById('answer');
    
    typeAnswer(answerText, answerEl, 30); 
});



    function startTyping() {
      if (window.typedInstance) {
        window.typedInstance.destroy();
      }

      window.typedInstance = new Typed('#typed', {
        strings: [
          'Your Smart Insurance, <span class="gradient-text">Policy Advisor</span>'
        ],
        typeSpeed: 60,
        backSpeed: 50,
        backDelay: 1000,
        showCursor: true,
        cursorChar: "|",
        contentType: 'html', // important for colored span
        onComplete: function(self) {
          setTimeout(startTyping, 10000);
        }
      });
    }

    document.addEventListener("DOMContentLoaded", startTyping);

  startTyping();












  /* ---------- Config & helpers ---------- */
 // use the Node server proxy (same origin when you open via http://localhost:3000)
const API_UPLOAD = '/api/upload';
const API_QUERY  = '/api/query';
const API_RECOMMEND = '/api/recommend';

function el(id){ return document.getElementById(id); }
function showToast(msg, kind='info'){
  // small inline toast using alert() for simplicity; replace with nicer UI if you want
  alert(msg);
}
function setLoading(btn, isLoading, originalText){
  if(!btn) return;
  if(isLoading){
    btn.disabled = true;
    btn.dataset.origText = originalText || btn.innerText;
    btn.innerText = 'Please wait...';
  } else {
    btn.disabled = false;
    btn.innerText = btn.dataset.origText || originalText || btn.innerText;
  }
}

/* ---------- How-It-Works interactive panels ---------- */
const askCard = document.getElementById('ask-anything-card');
const noPolicyCard = document.getElementById('no-policy-card');
const askPanel = document.getElementById('askPanel');
const howQueryInput = document.getElementById('howQueryInput');
const howQuerySend = document.getElementById('howQuerySend');
const howQueryResult = document.getElementById('howQueryResult');
const recPanel = document.getElementById('recPanel');
const recInput = document.getElementById('recInput');
const recSend = document.getElementById('recSend');
const recResult = document.getElementById('recResult');

function toggleAskPanel(show){
  if(askPanel) askPanel.style.display = show ? 'block' : 'none';
}

if(askCard){
  askCard.addEventListener('click', (e)=>{
    // prevent clicks on buttons from re-toggling
    if(e.target && (e.target.id === 'howQuerySend')) return;
    toggleAskPanel(true);
    if(howQueryInput) howQueryInput.focus();
  });
}
// Recommendations submit handler
if(recSend){
  recSend.addEventListener('click', async (e)=>{
    e.stopPropagation();
    const need = (recInput && recInput.value || '').trim();
    if(!need){ showToast('Please describe your needs.'); return; }
    setLoading(recSend, true, 'Get Recommendations');
    recResult.innerHTML = '<div class="text-center py-2">Finding recommendations...</div>';
    try {
      const data = await postJson(API_RECOMMEND, { need, top_n: 3 });
      renderInlineRecs(recResult, data);
    } catch(err){
      recResult.innerHTML = `<div class="text-danger">${escapeHtml(err.message)}</div>`;
    } finally {
      setLoading(recSend, false);
    }
  });
}

// Remove blanket focus on rec input to avoid stealing focus from Upload & Ask

async function postJson(url, payload){
  const resp = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
  const data = await resp.json().catch(()=>({ error:'Non-JSON response' }));
  if(!resp.ok){ throw new Error(data.error || `Request failed (${resp.status})`); }
  return data;
}

function renderInlineQA(target, data){
  if(!target) return;
  if(!data){ target.innerHTML = '<div>No response</div>'; return; }
  if(data.error){ target.innerHTML = `<div class="text-danger">Error: ${escapeHtml(data.error)}</div>`; return; }
  let html = `<div class="card p-2"><div class="fw-bold mb-1">Answer</div><div>${escapeHtml(data.answer || '')}</div>`;
  const refs = Array.isArray(data.refs) ? data.refs : [];
  if(refs.length){
    html += `<hr><div class="fw-bold">References</div>`;
    refs.forEach((r)=>{
      html += `<div class="small mb-2"><strong>${escapeHtml(r.policy || 'Unknown')}</strong> — Page ${escapeHtml(String(r.page || ''))} — Score: ${typeof r.score==='number'?r.score:''}<div class="text-muted">${escapeHtml(r.excerpt || '')}</div></div>`;
    });
  }
  html += `</div>`;
  target.innerHTML = html;
}

function renderInlineRecs(target, data){
  if(!target) return;
  if(!data){ target.innerHTML = '<div>No response</div>'; return; }
  if(data.error){ target.innerHTML = `<div class="text-danger">Error: ${escapeHtml(data.error)}</div>`; return; }
  const recs = Array.isArray(data.recs) ? data.recs : [];
  if(!recs.length){ target.innerHTML = '<div>No recommendations found.</div>'; return; }
  let html = `<div class="card p-2"><div class="fw-bold mb-2">Recommended Policies</div>`;
  recs.forEach((r)=>{
    const snips = (r.snips || []).map(s=>`<div class="text-muted small">• ${escapeHtml(s)}</div>`).join('');
    html += `<div class="mb-2"><div><strong>#${r.rank} ${escapeHtml(r.policy || '')}</strong> — Score: ${typeof r.score==='number'?r.score:''}</div>${snips}</div>`;
  });
  html += `</div>`;
  target.innerHTML = html;
}

if(howQuerySend){
  howQuerySend.addEventListener('click', async (e)=>{
    e.stopPropagation();
    const q = (howQueryInput && howQueryInput.value || '').trim();
    if(!q){ showToast('Please enter a question.'); return; }
    setLoading(howQuerySend, true, 'Send');
    howQueryResult.innerHTML = '<div class="text-center py-2">Searching...</div>';
    try {
      const data = await postJson(API_QUERY, { q, k: 5 });
      renderInlineQA(howQueryResult, data);
    } catch(err){
      howQueryResult.innerHTML = `<div class="text-danger">${escapeHtml(err.message)}</div>`;
    } finally {
      setLoading(howQuerySend, false);
    }
  });
}
/* ---------- Upload handler ---------- */
const uploadform = el('uploadForm');
const insuranceFile = el('insuranceFile');
const qasection = el('qaSection');
const askBtn = el('askBtn');
const userQuestion = el('userQuestion');
const answerDiv = el('answer');

if(uploadform){
  uploadform.addEventListener('submit', async (e) => {
    e.preventDefault();
    // TEMP for local testing: enable uploads
    const loggedIn = true; // change to real check in production
    if(!loggedIn){
      const authModal = new bootstrap.Modal(document.getElementById('authChoiceModal'));
      authModal.show();
      return;
    }

    const file = insuranceFile.files[0];
    if(!file){ showToast('Please choose a file to upload.'); return; }

    const btn = uploadform.querySelector('button[type="submit"]');
    setLoading(btn, true, btn.innerText);

    try {
      const fd = new FormData();
      fd.append('file', file);

      const resp = await fetch(API_UPLOAD, { method: 'POST', body: fd });
      if(!resp.ok){
        const err = await resp.json().catch(()=>({error:'Upload failed'}));
        throw new Error(err.error || 'Upload failed');
      }
      const j = await resp.json();
      showToast(`Uploaded: ${j.filename || 'file'}. Index ready.`,'success');
       
      // Reveal QA UI for uploaded document
      qasection.style.display = 'block';
      // Ensure recommendation panel is hidden after upload to avoid confusion
      const recPanelEl = document.getElementById('recPanel');
      if(recPanelEl) recPanelEl.style.display = 'none';
      // Also show the Ask Anything panel
      try { toggleAskPanel(true); } catch(_){}
      userQuestion.focus();
      insuranceFile.value = '';
    } catch (err){
      console.error('Upload error', err);
      showToast('Upload failed: ' + err.message, 'error');
    } finally {
      setLoading(uploadform.querySelector('button[type="submit"]'), false);
    }
  });
}

/* ---------- Ask / QA handler ---------- */
if(askBtn){
  askBtn.addEventListener('click', async (e) => {
    e.preventDefault();
    const q = userQuestion.value && userQuestion.value.trim();
    if(!q){ showToast('Please enter a question.'); return; }
    setLoading(askBtn, true, 'Ask');

    answerDiv.innerHTML = `<div class="text-center py-3">Searching for answers...</div>`;

    try {
      const resp = await fetch(API_QUERY, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ q, k: 5 })
      });
      if(!resp.ok){
        const err = await resp.json().catch(()=>({error:'Query failed'}));
        throw new Error(err.error || 'Query failed');
      }
      const data = await resp.json();
      console.log('Data received from /api/query:', data);
      renderAnswer(data);
    } catch (err){
      console.error('Query error', err);
      answerDiv.innerHTML = `<div class="text-danger">Error: ${err.message}</div>`;
    } finally {
      setLoading(askBtn, false);
    }
  });
}

function renderAnswer(data){
  if(!data){ answerDiv.innerHTML = `<div>No response</div>`; return; }
  if(data.error){
    answerDiv.innerHTML = `<div class="text-danger">Error: ${data.error}</div>`; return;
  }
  console.log('Response from backend:', data);
  console.log('Answer to display:', data.answer);

  const answerText = data.answer || 'Here are the results:';
  let html = `<div class="card p-3"><h5>Answer</h5><p>${escapeHtml(answerText)}</p>`;

  const refs = data.refs || [];
  const fulls = data.full_texts || [];

  if(Array.isArray(refs) && refs.length){
    html += `<hr><h6>References</h6>`;
    refs.forEach((r, idx) => {
      html += `<div class="mb-3 ref-item" id="ref-${idx}">
        <div><strong>${escapeHtml(r.policy || 'Unknown')}</strong> — Page ${escapeHtml(String(r.page || ''))} — Score: ${r.score ?? ''}</div>
        <div class="small text-muted mt-1 excerpt">${escapeHtml(r.excerpt || '')}</div>
      </div>`;
    });
  }

  html += `</div>`;
  answerDiv.innerHTML = html;

  // wire up show-more behaviors for this render using local "fulls"
  document.querySelectorAll('.show-more').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      const idx = Number(btn.dataset.idx);
      const full = (fulls[idx] && fulls[idx].full_text) ? fulls[idx].full_text : null;
      const clause = (fulls[idx] && fulls[idx].clause_text) ? fulls[idx].clause_text : null;

      const container = document.getElementById(`ref-${idx}`);
      if(!container) return;

      if(btn.dataset.expanded === '1'){
        const existingFull = container.querySelector('.full-text');
        if(existingFull) existingFull.remove();
        btn.textContent = 'Show more';
        btn.dataset.expanded = '0';
      } else {
        let moreHtml = '<div class="full-text mt-2 p-2 border rounded bg-light">';
        if(clause){
          moreHtml += `<div class="fw-bold mb-1">Clause context:</div><div class="small mb-2">${escapeHtml(clause)}</div>`;
        }
        if(full){
          moreHtml += `<div class="full-body small">${escapeHtml(full)}</div>`;
        } else {
          moreHtml += `<div class="text-muted small">Full text not available.</div>`;
        }
        moreHtml += '</div>';
        container.insertAdjacentHTML('beforeend', moreHtml);
        btn.textContent = 'Show less';
        btn.dataset.expanded = '1';
      }
    });
  });
}


/* ---------- FAQ Search ---------- */
const faqSearch = el('faqSearch');
if(faqSearch){
  faqSearch.addEventListener('input', (e)=>{
    const q = e.target.value.trim().toLowerCase();
    const items = document.querySelectorAll('#faqAccordion .accordion-item');
    items.forEach(it=>{
      const txt = it.innerText.toLowerCase();
      if(!q || txt.includes(q)) it.style.display = '';
      else it.style.display = 'none';
    });
  });
}

/* ---------- Small utilities ---------- */
function escapeHtml(unsafe){
  if(!unsafe && unsafe !== 0) return '';
  return String(unsafe)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}



