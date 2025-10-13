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
      strings: ["Understand Your Insurance Papers Instantly"],
      typeSpeed: 70,
      backSpeed: 40,
      backDelay: 1000,
      showCursor: true,
      cursorChar: "|",
      onComplete: function(self) {
        setTimeout(function() {
          startTyping(); 
        }, 10000);
      }
    });
  }

  startTyping();