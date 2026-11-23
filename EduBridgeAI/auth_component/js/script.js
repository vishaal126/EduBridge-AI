/* =====================================================================
   EduBridge AI sign-in screen: runs inside a Streamlit custom component.

   This file is UI only. It validates for convenience and sends every action
   (login, register, social start, reset...) to the Python backend through the
   Streamlit component bridge. The server does ALL real checks (passwords,
   roles, OAuth, tokens, sessions). Nothing here stores accounts, hashes
   passwords or decides that anyone is signed in.
   ===================================================================== */
(() => {
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const getRole = () => $('.seg').dataset.v;
const rand = n => [...crypto.getRandomValues(new Uint8Array(n))].map(b => b.toString(16).padStart(2, '0')).join('');

/* ---------- Streamlit bridge (component protocol v1, no build step) ---------- */
const Bridge = (() => {
  const pending = new Map(), orphaned = new Set();
  const inflight = {
    get() { try { return JSON.parse(sessionStorage.getItem('eb_inflight') || 'null'); } catch { return null; } },
    set(id, action) { try { sessionStorage.setItem('eb_inflight', JSON.stringify({ id, action })); } catch {} },
    clear(id) { try { const f = JSON.parse(sessionStorage.getItem('eb_inflight') || 'null'); if (!f || f.id === id) sessionStorage.removeItem('eb_inflight'); } catch {} }
  };
  const post = (type, extra) => parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, extra), '*');
  const setValue = (action, payload) => {
    const id = rand(8);
    post('streamlit:setComponentValue', { value: Object.assign({ id, action }, payload), dataType: 'json' });
    return id;
  };
  return {
    init(onArgs) {
      addEventListener('message', e => {
        if (e.source !== parent) return;
        const d = e.data || {};
        if (d.type !== 'streamlit:render') return;
        const a = d.args || {}, r = a.result;
        if (r && r.id && pending.has(r.id)) { pending.get(r.id)(r); pending.delete(r.id); inflight.clear(r.id); }
        else if (r && r.id) {
          const f = inflight.get();
          if (f && f.id === r.id && !orphaned.has(r.id)) { orphaned.add(r.id); inflight.clear(r.id); setTimeout(() => applyOrphan(f.action, r), 0); }
        }
        onArgs(a);
      });
      post('streamlit:componentReady', { apiVersion: 1 });
      post('streamlit:setFrameHeight', { height: 820 });
    },
    send(action, payload = {}) { setValue(action, payload); },
    call(action, payload = {}) {
      return new Promise(resolve => {
        const id = setValue(action, payload);
        inflight.set(id, action);
        const t = setTimeout(() => { pending.delete(id); inflight.clear(id); resolve({ ok: false, error: 'The server did not respond. Please try again.' }); }, 30000);
        pending.set(id, r => { clearTimeout(t); resolve(r); });
      });
    }
  };
})();

/* ---------- Cookies (the server decides what they mean) ---------- */
const secure = location.protocol === 'https:' ? '; Secure' : '';
const setCookie = (n, v, maxAge, same = 'Strict') => { document.cookie = `${n}=${encodeURIComponent(v)}; Path=/; SameSite=${same}${maxAge ? '; Max-Age=' + maxAge : ''}${secure}`; };
const delCookie = n => { document.cookie = `${n}=; Path=/; Max-Age=0; SameSite=Lax${secure}`; };

/* ---------- Safe localStorage wrapper ---------- */
const store = {
  get: k => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
  del: k => { try { localStorage.removeItem(k); } catch {} }
};

/* ---------- Toasts ---------- */
const Toast = {
  show(msg, type = 'info') {
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.innerHTML = '<svg class="i" style="flex:none;color:' + (type === 'error' ? 'var(--err)' : type === 'success' ? 'var(--ok)' : 'var(--cyan)') + '"><use href="#' + (type === 'error' ? 'bad' : 'ok') + '"/></svg><span></span>';
    t.lastChild.textContent = msg;
    $('#toasts').appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, 4200);
  }
};

/* ---------- Validation rules (convenience only; the server re-checks everything) ---------- */
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const PHONE = /^\+?[\d\s().-]{8,20}$/;
const rules = {
  identity: v => { v = v.trim(); const d = v.replace(/\D/g, '').length; return !v ? 'Enter your email or mobile number.' : (EMAIL.test(v) || (PHONE.test(v) && d >= 8 && d <= 15)) ? '' : 'Enter a valid email address or mobile number.'; },
  loginpw: v => v ? '' : 'Enter your password.',
  name: v => v.trim().length >= 2 ? '' : 'Enter your full name.',
  email: v => !v.trim() ? 'Enter your email address.' : EMAIL.test(v) ? '' : 'That email address looks incomplete.',
  newpw: v => v.length < 8 ? 'Use at least 8 characters.' : strength(v).score < 3 ? 'Mix upper and lower case letters with a number or symbol.' : '',
  confirm: (v, form) => v && v === $('[data-rule="newpw"] input', form).value ? '' : 'Passwords do not match.',
  terms: (v, form, el) => $('input', el).checked ? '' : 'Accept the terms to continue.'
};

/* ---------- Password strength (0-4) ---------- */
function strength(pw) {
  let s = 0;
  if (pw.length >= 8) s++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s++;
  if (/\d/.test(pw)) s++;
  if (/[^\w\s]/.test(pw) || pw.length >= 14) s++;
  return { score: pw ? Math.max(1, s) : 0, label: ['', 'Weak', 'Fair', 'Good', 'Strong'][pw ? Math.max(1, s) : 0] };
}

/* ---------- Field helpers ---------- */
function setState(field, error, show = true) {
  const input = $('input', field);
  const touched = input.type === 'checkbox' ? true : input.value !== '';
  field.classList.toggle('err', show && !!error);
  field.classList.toggle('ok', show && !error && touched && input.type !== 'checkbox');
  $('.msg', field).textContent = show ? error : '';
  const tick = $('.tick use', field);
  if (tick) tick.setAttribute('href', error ? '#bad' : '#ok');
}
function check(field, form, show = true) {
  const input = $('input', field);
  const err = rules[field.dataset.rule](input.value, form, field);
  setState(field, err, show);
  return !err;
}
function validateForm(form) {
  let first = null;
  const ok = $$('.field[data-rule]', form).map(f => {
    const good = check(f, form);
    if (!good && !first) first = $('input', f);
    return good;
  }).every(Boolean);
  if (!ok) { first && first.focus(); shake(form.closest('.card') || form); }
  return ok;
}
function shake(el) { el.classList.remove('shake'); void el.offsetWidth; el.classList.add('shake'); }

/* Real-time validation + strength meters */
$$('form').forEach(form => {
  $$('.field[data-rule]', form).forEach(f => {
    const input = $('input', f);
    // Validate live once the user has typed; checkboxes validate on change.
    input.addEventListener(input.type === 'checkbox' ? 'change' : 'input', () => {
      const live = input.type === 'checkbox' || input.value !== '' || f.classList.contains('err') || f.classList.contains('ok');
      if (live) check(f, form);
      if (f.dataset.rule === 'newpw') { const c = $('[data-rule="confirm"]', form); if ($('input', c).value) check(c, form); }
    });
    input.addEventListener('blur', () => { if (input.value && input.type !== 'checkbox') check(f, form); });
    const meter = $('.meter', f);
    if (meter) input.addEventListener('input', () => {
      const s = strength(input.value);
      meter.dataset.s = s.score; $('em', meter).textContent = s.label;
    });
  });
});

/* Show/hide password */
$$('.eye').forEach(btn => btn.addEventListener('click', () => {
  const input = $('input', btn.parentElement), show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  $('use', btn).setAttribute('href', show ? '#eyeoff' : '#eye');
  btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  input.focus();
}));

/* ---------- Button loading helper ---------- */
async function withLoading(btn, fn) {
  btn.classList.add('loading'); btn.disabled = true;
  try { return await fn(); } finally { btn.classList.remove('loading'); btn.disabled = false; }
}

/* ---------- Confetti burst ---------- */
function confetti(x, y) {
  if (reduce) return;
  const cols = ['#22e6ff', '#8b6bff', '#2f7bff', '#ff5fd2', '#2cf5a6'];
  for (let i = 0; i < 44; i++) {
    const c = document.createElement('i'); c.className = 'confetti';
    c.style.left = x + 'px'; c.style.top = y + 'px'; c.style.background = cols[i % 5];
    document.body.appendChild(c);
    const a = Math.random() * Math.PI * 2, d = 120 + Math.random() * 240;
    c.animate([{ transform: 'translate(0,0) rotate(0)', opacity: 1 }, { transform: `translate(${Math.cos(a) * d}px,${Math.sin(a) * d + 160}px) rotate(${Math.random() * 720}deg)`, opacity: 0 }],
      { duration: 1100 + Math.random() * 700, easing: 'cubic-bezier(.1,.7,.3,1)' }).onfinish = () => c.remove();
  }
}
/* ---------- Server responses -> form state ---------- */
const FIELD = { identity: 'identity', name: 'name', email: 'email', confirm: 'confirm', terms: 'terms' };
function showErr(form, res) {
  shake($('#card'));
  const rule = res.field === 'password' ? ($('[data-rule="newpw"]', form) ? 'newpw' : 'loginpw') : FIELD[res.field];
  const f = rule && $('[data-rule="' + rule + '"]', form);
  if (f) setState(f, res.error);
  Toast.show(res.error || 'Something went wrong. Please try again.', 'error');
}
const clearForm = f => { f.reset(); $$('.field', f).forEach(x => { x.classList.remove('ok', 'err'); const m = $('.msg', x); m && (m.textContent = ''); }); $$('.meter', f).forEach(m => { m.dataset.s = 0; $('em', m).textContent = ''; }); };

/* ---------- Signed in (the SERVER already created the session) ---------- */
let proceeding = false;
function succeed(res) {
  if (res.cookie) setCookie('eb_rt', res.cookie.token, res.cookie.persistent ? 30 * 86400 : 0);
  const first = (res.user.name || res.user.email || '').split(/[\s@]/)[0];
  $('#doneT').textContent = (res.created ? 'Welcome, ' : 'Welcome back, ') + first;
  $('#doneMsg').textContent = 'Signed in as ' + (res.user.email || res.user.name) + '. Opening your ' + res.user.role + ' workspace…';
  Modal.closeAll();
  $('#done').classList.add('show');
  const r = $('#card').getBoundingClientRect(); confetti(r.left + r.width / 2, r.top + r.height / 2);
  Toast.show('Signed in successfully.', 'success');
  const go = () => { if (proceeding) return; proceeding = true; Bridge.send('continue'); };
  $('#goNow').onclick = go; setTimeout(go, 1500); $('#goNow').focus();
}

function applyOrphan(action, res) {
  const forms = { login: loginForm, register: regForm, link_confirm: linkForm, reset: resetForm, forgot: forgotForm };
  const form = forms[action];
  $$('.btn.loading').forEach(b => { b.classList.remove('loading'); b.disabled = false; });
  if (!res.ok) { form ? showErr(form, res) : Toast.show(res.error, 'error'); return; }
  if (action === 'login' || action === 'register' || action === 'link_confirm') { form && clearForm(form); succeed(res); }
  else if (action === 'forgot') { Toast.show(res.message, 'success'); Modal.close($('#forgot'), true); }
  else if (action === 'reset') { Toast.show('Password updated. Sign in with your new password.', 'success'); Modal.close($('#reset'), true); }
}

/* ---------- Login ---------- */
const loginForm = $('#loginForm');
loginForm.addEventListener('submit', async e => {
  e.preventDefault();
  if (!validateForm(loginForm)) { Toast.show('Fix the highlighted fields to continue.', 'error'); return; }
  const ident = $('#lid').value.trim(), remember = $('#remember').checked;
  const res = await withLoading($('.btn', loginForm), () => Bridge.call('login', { ident, password: $('#lpw').value, role: getRole(), remember }));
  if (!res.ok) return showErr(loginForm, res);
  remember ? store.set('eb_email', ident) : store.del('eb_email');
  clearForm(loginForm);
  succeed(res);
});
const savedId = store.get('eb_email');
if (savedId) { $('#lid').value = savedId; $('#remember').checked = true; check($('[data-rule="identity"]', loginForm), loginForm); }

/* ---------- Registration ---------- */
const regForm = $('#regForm');
regForm.addEventListener('submit', async e => {
  e.preventDefault();
  if (!validateForm(regForm)) { Toast.show('Fix the highlighted fields to continue.', 'error'); return; }
  const payload = { name: $('#rn').value, email: $('#re').value, password: $('#rp').value, confirm: $('#rc').value, role: getRole(), terms: $('#rt').checked,
    profile: { skills: $('#rsk').value, wants: $('#rwn').value, city: $('#rci').value, state: $('#rst').value, country: $('#rco').value } };
  const res = await withLoading($('.btn', regForm), () => Bridge.call('register', payload));
  if (!res.ok) return showErr(regForm, res);
  clearForm(regForm); $('#rco').value = 'India';
  succeed(res);
});

/* ---------- Smooth view switching ---------- */
const Views = {
  go(name) {
    const from = $('.view:not([hidden])'), to = $('#' + name + 'View');
    if (!from || from === to) return;
    from.classList.add('leave');
    setTimeout(() => {
      from.hidden = true; from.classList.remove('leave'); to.hidden = false;
      to.style.animation = 'none'; void to.offsetWidth; to.style.animation = '';
      const f = $('input', to); f && f.focus({ preventScroll: true });
    }, reduce ? 0 : 280);
  }
};
$$('[data-view]').forEach(b => b.addEventListener('click', () => Views.go(b.dataset.view)));

/* ---------- Modals ---------- */
const Modal = {
  open(id) {
    const m = $('#' + id); if (m.classList.contains('open')) return;
    m.classList.add('open'); Modal.last = document.activeElement;
    setTimeout(() => { const t = $('input', m); t && t.focus(); }, 60);
  },
  close(m, quiet) {
    if (!m.classList.contains('open')) return;
    m.classList.remove('open');
    if (!quiet) { if (m.id === 'link') Bridge.send('link_cancel'); if (m.id === 'reset') Bridge.send('reset_cancel'); }
    if (m.id === 'forgot') setTimeout(() => clearForm(forgotForm), 350);
    Modal.last && Modal.last.focus && Modal.last.focus();
  },
  closeAll() { $$('.modal.open').forEach(m => Modal.close(m, true)); }
};
$$('[data-open]').forEach(b => b.addEventListener('click', () => Modal.open(b.dataset.open)));
$$('.modal').forEach(m => m.addEventListener('click', e => { if (e.target === m || e.target.closest('[data-close]')) Modal.close(m); }));
addEventListener('keydown', e => { if (e.key === 'Escape') $$('.modal.open').forEach(m => Modal.close(m)); });

/* ---------- Forgot / reset password (single-use emailed token, handled by the server) ---------- */
const forgotForm = $('#forgotForm'), resetForm = $('#resetForm'), linkForm = $('#linkForm');
forgotForm.addEventListener('submit', async e => {
  e.preventDefault();
  if (!validateForm(forgotForm)) return;
  const res = await withLoading($('.btn', forgotForm), () => Bridge.call('forgot', { email: $('#fe').value }));
  if (!res.ok) return showErr(forgotForm, res);
  Toast.show(res.message, 'success'); Modal.close($('#forgot'), true); clearForm(forgotForm);
});
resetForm.addEventListener('submit', async e => {
  e.preventDefault();
  if (!validateForm(resetForm)) return;
  const res = await withLoading($('.btn', resetForm), () => Bridge.call('reset', { password: $('#np').value, confirm: $('#nc').value }));
  if (!res.ok) { showErr(resetForm, res); if (res.fatal) Modal.close($('#reset'), true); return; }
  Toast.show('Password updated. Sign in with your new password.', 'success');
  Modal.close($('#reset'), true); clearForm(resetForm);
  if (res.email) { $('#lid').value = res.email; $('#lpw').value = ''; check($('[data-rule="identity"]', loginForm), loginForm); }
});
linkForm.addEventListener('submit', async e => {
  e.preventDefault();
  if (!validateForm(linkForm)) return;
  const res = await withLoading($('.btn', linkForm), () => Bridge.call('link_confirm', { password: $('#lkp').value }));
  if (!res.ok) { showErr(linkForm, res); if (res.fatal) Modal.close($('#link'), true); return; }
  clearForm(linkForm); succeed(res);
});

/* ---------- Button ripple ---------- */
document.addEventListener('pointerdown', e => {
  const b = e.target.closest('.btn,.sbtn'); if (!b) return;
  const r = b.getBoundingClientRect(), d = Math.max(r.width, r.height), s = document.createElement('span');
  s.className = 'ripple'; s.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX - r.left - d / 2}px;top:${e.clientY - r.top - d / 2}px`;
  b.appendChild(s); setTimeout(() => s.remove(), 600);
});

/* ---------- Mouse spotlight + 3D card tilt ---------- */
const spot = $('.spot'), card = $('#card');
addEventListener('pointermove', e => { spot.style.setProperty('--mx', e.clientX + 'px'); spot.style.setProperty('--my', e.clientY + 'px'); });
if (!reduce && matchMedia('(hover:hover)').matches) {
  card.addEventListener('pointermove', e => {
    const r = card.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
    card.style.transform = `rotateY(${x * 8}deg) rotateX(${-y * 8}deg)`;
  });
  card.addEventListener('pointerleave', () => card.style.transform = '');
}

/* ---------- Card cursor glow ---------- */
card.addEventListener('pointermove', e => {
  const r = card.getBoundingClientRect();
  card.style.setProperty('--cx', (e.clientX - r.left) + 'px'); card.style.setProperty('--cy', (e.clientY - r.top) + 'px');
});

/* ---------- Magnetic buttons ---------- */
if (!reduce && matchMedia('(hover:hover)').matches) {
  $$('.btn').forEach(b => {
    b.addEventListener('pointermove', e => { const r = b.getBoundingClientRect(); b.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * .06}px,${(e.clientY - r.top - r.height / 2) * .22 - 3}px)`; });
    b.addEventListener('pointerleave', () => b.style.transform = '');
  });
  // Sphere core follows the cursor slightly
  addEventListener('pointermove', e => {
    const core = $('.core'); core.style.setProperty('--px', ((e.clientX / innerWidth - .5) * 26) + 'px'); core.style.setProperty('--py', ((e.clientY / innerHeight - .5) * 26) + 'px');
  });
}

/* ---------- Role switch (Student / Tutor) ---------- */
(() => {
  const segs = $$('.seg'), lead = $('#loginView .lead'), lbl = $('#loginForm .lbl');
  const copy = { Student: ['Sign in to continue your journey.', 'Sign in'], Tutor: ['Sign in to your tutor dashboard.', 'Sign in as tutor'] };
  const set = r => {
    segs.forEach(seg => { seg.dataset.v = r; $$('button', seg).forEach(b => { const on = b.dataset.role === r; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); }); });
    lead.textContent = copy[r][0]; lbl.textContent = copy[r][1]; store.set('eb_role', r);
  };
  segs.forEach(seg => $$('button', seg).forEach(b => b.addEventListener('click', () => set(b.dataset.role))));
  set(store.get('eb_role') === 'Tutor' ? 'Tutor' : 'Student');
})();

/* ---------- Demo chat: alternate Telugu and Hindi ---------- */
(() => {
  const L = [
    ['Quadratic equations నాకు అర్థం కావడం లేదు', 'Finding a Telugu-speaking maths tutor…', 'AR', 'Ananya R. is free now', 'Algebra, Telugu + English, 4.9 rating'],
    ['Quadratic equations मुझे समझ नहीं आ रहे हैं', 'Finding a Hindi-speaking maths tutor…', 'RS', 'Rohan S. is free now', 'Algebra, Hindi + English, 4.8 rating']
  ];
  const d1 = $('.d1'); if (!d1) return; let i = 0;
  d1.addEventListener('animationiteration', () => {
    const l = L[i = (i + 1) % L.length];   // swaps while the chat is faded out
    d1.textContent = l[0]; $('.d2').lastChild.textContent = l[1];
    $('.av').textContent = l[2]; $('.match b').textContent = l[3]; $('.match small').textContent = l[4];
  });
})();

/* ---------- Rotating tagline ---------- */
(() => {
  const el = $('#rot'), lines = ['Ask doubts in Telugu', 'Ask doubts in Hindi', 'Match with the right tutor', 'Swap skills with peers', 'Get smart notes after every session']; let i = 0;
  if (reduce || !el) return;
  setInterval(() => { el.classList.add('out'); setTimeout(() => { el.textContent = lines[i = (i + 1) % lines.length]; el.classList.remove('out'); }, 450); }, 2800);
})();

/* ---------- Caps Lock warning ---------- */
['#lpw', '#rp', '#rc'].forEach(id => {
  const input = $(id), note = document.createElement('div');
  note.className = 'caps'; note.textContent = 'Caps Lock is on'; input.closest('.field').appendChild(note);
  const upd = e => note.classList.toggle('on', !!(e.getModifierState && e.getModifierState('CapsLock')));
  input.addEventListener('keydown', upd); input.addEventListener('keyup', upd); input.addEventListener('blur', () => note.classList.remove('on'));
});

/* ---------- Animated stat counters ---------- */
$$('[data-count]').forEach(el => {
  const end = +el.dataset.count, suf = el.dataset.suffix || '', t0 = performance.now() + 500;
  const tick = t => {
    const p = Math.min(1, Math.max(0, (t - t0) / 1400)), v = Math.round(end * (1 - Math.pow(1 - p, 3)));
    el.textContent = v + suf; if (p < 1) requestAnimationFrame(tick);
  };
  reduce ? el.textContent = end + suf : requestAnimationFrame(tick);
});

/* ---------- Floating particles (canvas) with link lines + mouse repulsion ---------- */
(() => {
  const cv = $('#fx'), ctx = cv.getContext('2d'); let W, H, P = [], mouse = { x: -999, y: -999 };
  const colors = ['34,230,255', '139,107,255', '47,123,255', '255,95,210'];
  function size() {
    const dpr = Math.min(devicePixelRatio || 1, 2); W = cv.width = innerWidth * dpr; H = cv.height = innerHeight * dpr;
    cv.style.width = innerWidth + 'px'; cv.style.height = innerHeight + 'px'; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.min(90, Math.floor(innerWidth * innerHeight / 16000));
    P = Array.from({ length: n }, () => ({ x: Math.random() * innerWidth, y: Math.random() * innerHeight, vx: (Math.random() - .5) * .35, vy: (Math.random() - .5) * .35, r: Math.random() * 1.8 + .6, c: colors[(Math.random() * 4) | 0] }));
  }
  addEventListener('resize', size); addEventListener('pointermove', e => { mouse.x = e.clientX; mouse.y = e.clientY; });
  size();
  function frame() {
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    for (const p of P) {
      const dx = p.x - mouse.x, dy = p.y - mouse.y, d = Math.hypot(dx, dy);
      if (d < 120) { p.x += dx / d * 1.2; p.y += dy / d * 1.2; }
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > innerWidth) p.vx *= -1; if (p.y < 0 || p.y > innerHeight) p.vy *= -1;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.283); ctx.fillStyle = `rgba(${p.c},.9)`; ctx.shadowColor = `rgb(${p.c})`; ctx.shadowBlur = 10; ctx.fill();
    }
    ctx.shadowBlur = 0;
    for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {
      const d = Math.hypot(P[i].x - P[j].x, P[i].y - P[j].y);
      if (d < 110) { ctx.strokeStyle = `rgba(${P[i].c},${.16 * (1 - d / 110)})`; ctx.beginPath(); ctx.moveTo(P[i].x, P[i].y); ctx.lineTo(P[j].x, P[j].y); ctx.stroke(); }
    }
    if (!reduce) requestAnimationFrame(frame);
  }
  frame();
})();

/* ---------- Server -> screen: runs on every Streamlit render ---------- */
const handled = new Set();
let resetOpen = false, linkOpen = false;
function onArgs(a) {
  if (a.clear && a.clear.id && !handled.has(a.clear.id)) { handled.add(a.clear.id); (a.clear.names || []).forEach(delCookie); }   // one-shot
  const f = a.flash;
  if (f && f.id && !handled.has(f.id)) {                     // result of a redirect-based action (OAuth callback, reset link)
    handled.add(f.id);
    if (f.ok && f.user) succeed(f); else if (f.error) Toast.show(f.error, 'error'); else if (f.message) Toast.show(f.message, f.type || 'info');
  }
  if (a.mode === 'reset' && !resetOpen) { resetOpen = true; Modal.open('reset'); }
  if (a.mode !== 'reset') resetOpen = false;
  if (a.link && !linkOpen) {
    linkOpen = true;
    $('#linkMsg').textContent = 'An EduBridge account already exists for ' + a.link.email + '. Enter its password once to link your ' + a.link.provider + ' account.';
    Modal.open('link');
  }
  if (!a.link) linkOpen = false;
}
Bridge.init(onArgs);
})();
