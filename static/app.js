'use strict';
// Chat scheduling assistant. Plain JS, no build step. Server strings only ever go in as text nodes.
(() => {
  const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const UNDO_SECONDS = 8; // a cancel waits this long in the browser before it is sent
  const YES = /^\s*(y|yes)[.!]?\s*$/i; // same as the server's typed-confirm pattern
  // The server is stateless: the browser holds the chat history and sends it back each turn.
  let history = [];
  const keepHistory = r => { if (r && Array.isArray(r.history)) history = r.history; };
  // The server doesn't stop a card running twice, so the browser does.
  const used = new Set();
  function sendCard(c) {
    if (used.has(c.change_id)) return null;
    used.add(c.change_id);
    return api('/api/confirm', () => ({ card: c, history })).then(r => { keepHistory(r); return r; });
  }
  const ERR = "Couldn't reach the server. Try again.";
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = s => document.querySelector(s);

  // Static icon markup (never contains server data).
  const ARROW = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M3 7h8M7.5 3.5L11 7l-3.5 3.5" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const SWAP = '<svg width="28" height="16" viewBox="0 0 28 16" fill="none" aria-hidden="true"><path d="M5 8h18M18.5 3.5L23 8l-4.5 4.5" stroke="#050505" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const CHECK = '<svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3 4.7-5" stroke="#050DC8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ALERT = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke="#050505" stroke-width="1.3"/><path d="M8 4.8v3.6" stroke="#050505" stroke-width="1.4" stroke-linecap="round"/><circle cx="8" cy="10.9" r=".8" fill="#050505"/></svg>';
  const CLOSE = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
  const INFO = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><circle cx="7" cy="7" r="5.75" stroke="currentColor" stroke-width="1.2"/><path d="M7 6.3v3.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="7" cy="4.4" r=".75" fill="currentColor"/></svg>';
  const chev = c => `<svg class="chev" width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true"><path d="M2 3.5L5 6.5l3-3" stroke="${c}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'icon') el.insertAdjacentHTML('beforeend', v);
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c != null && c !== false && c !== '') el.append(c instanceof Node ? c : String(c));
    return el;
  }
  const icon = (svg, cls) => h('span', { class: cls, icon: svg });

  // ---------- time ----------
  const pad = n => String(n).padStart(2, '0');
  const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const todayStr = () => ymd(new Date());
  const nowMin = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); };
  const toDate = s => new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));
  const minOf = s => +s.slice(11, 13) * 60 + +s.slice(14, 16);
  const hm = m => { const x = ((m % 1440) + 1440) % 1440; return `${(Math.floor(x / 60) % 12) || 12}:${pad(x % 60)}`; };
  const ap = m => (((m % 1440) + 1440) % 1440) < 720 ? 'AM' : 'PM';
  const range = (a, b) => `${hm(a)}–${hm(b)}`;
  const rangeAP = (a, b) => ap(a) === ap(b) ? `${hm(a)}–${hm(b)} ${ap(b)}` : `${hm(a)} ${ap(a)} – ${hm(b)} ${ap(b)}`;
  const dur = m => m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ''}`;
  const weekday = s => toDate(s).toLocaleDateString('en-US', { weekday: 'long' });
  function dayLabel(s) {
    const t = toDate(todayStr()), d = toDate(s), diff = Math.round((d - t) / 864e5);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Tomorrow';
    return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  }
  function norm(b) {
    const s = minOf(b.start);
    const days = Math.round((toDate(b.end) - toDate(b.start)) / 864e5);
    return { uid: b.uid, title: b.title, start: b.start, date: b.start.slice(0, 10), s, e: minOf(b.end) + days * 1440 };
  }

  // ---------- state ----------
  const S = {
    day: todayStr(), bookings: [], loaded: false, error: false,
    cards: new Map(),     // change_id -> open card (drawn as pending on the panel)
    cands: new Set(),     // booking uids outlined as "which one?" candidates
    tags: {},             // uid -> "New" | "Moved" | "Restored"
    hidden: new Set(),    // uids cancelled in this session (the server may still list them during undo)
    busy: false, freeOpen: false, phoneOpen: false, name: '',
  };

  // The session is an httpOnly cookie, so requests carry no session id.
  // One request at a time: the server may renew the cal.com token on any call, and two
  // renewals racing can retire each other's token and sign the visitor out.
  let chain = Promise.resolve();
  let queued = 0;
  function api(path, body) {
    queued++;
    const run = chain.then(() => request(path, body)).finally(() => { queued--; });
    chain = run.catch(() => {});
    return run;
  }
  const fail = (msg, status) => Object.assign(new Error(msg), { user: msg, status });
  // body may be a function so queued requests read `history` when they actually go out, not when queued.
  async function request(path, bodyOrFn) {
    const body = typeof bodyOrFn === 'function' ? bodyOrFn() : bodyOrFn;
    const opt = body
      ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tz: TZ, ...body }) }
      : { credentials: 'same-origin' };
    let r;
    try { r = await fetch(path, opt); } catch (e) { throw fail(ERR); }
    if (r.status === 401) { showConnect(); throw fail('', 401); }
    if (r.status === 422) throw fail("That didn't go through. Try again.", 422);
    if (r.status === 502 || r.status === 503) {
      let detail = '';
      try { detail = (await r.json()).detail; } catch (e) { /* no JSON body */ }
      throw fail(typeof detail === 'string' && detail ? detail : ERR, r.status);
    }
    if (!r.ok) throw fail('Something went wrong. Try again.', r.status);
    return r.json();
  }
  const errText = e => (e && e.user !== undefined ? e.user : ERR);

  // ---------- sign-in ----------
  function showConnect() {
    closeToast();
    $('#app').hidden = true;
    $('#menu').hidden = true;
    $('#connect').hidden = false;
  }
  const initials = n => n.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('') || '·';
  function toggleMenu(e) {
    e.stopPropagation();
    const menu = $('#menu');
    menu.hidden = !menu.hidden;
    document.querySelectorAll('.acct').forEach(b => b.setAttribute('aria-expanded', String(!menu.hidden)));
  }
  const acctButton = () => h('button', { class: 'acct', type: 'button', 'aria-label': 'Account', 'aria-haspopup': 'true',
    'aria-expanded': String(!$('#menu').hidden), onclick: toggleMenu }, initials(S.name));

  // ---------- today panel ----------
  function matchBooking(c, list) {
    if (!c.old_start) return null;
    const at = list.filter(b => b.start === c.old_start);
    return at.find(b => b.title === c.title) || (at.length === 1 ? at[0] : null);
  }

  function model() {
    const isToday = S.day === todayStr(), now = isToday ? nowMin() : -1;
    const items = S.bookings.filter(b => !S.hidden.has(b.uid)).map(norm).filter(b => b.date === S.day);
    const pend = c => {
      const s = minOf(c.new_start);
      return { uid: `p-${c.change_id}`, title: c.title, s, e: s + (+c.minutes || 30), v: 'pend', tag: 'Pending', virtual: true };
    };
    for (const c of S.cards.values()) {
      const b = matchBooking(c, items);
      if (c.kind === 'cancel') { if (b) b.v = 'sel'; continue; }
      if (c.kind === 'reschedule' && b) b.v = 'ghost';
      if (c.new_start && c.new_start.slice(0, 10) === S.day) items.push(pend(c));
    }
    for (const b of items) if (!b.v && S.cands.has(b.uid)) b.v = 'cand';
    items.sort((a, b) => a.s - b.s || a.title.localeCompare(b.title));

    const real = items.filter(b => !b.virtual);
    const past = b => isToday && !b.virtual && b.e <= now;
    const next = isToday ? real.find(b => !past(b)) || null : null;
    const later = items.filter(b => !past(b) && b !== next);
    const remaining = real.filter(b => !past(b));
    const lo = Math.min(540, ...items.map(b => Math.floor(b.s / 60) * 60));
    const hi = Math.max(1080, ...items.map(b => Math.ceil(b.e / 60) * 60));
    const m = { isToday, now, items, next, later, lo, hi, past, left: remaining.length };

    const last = (isToday ? remaining : real).reduce((x, b) => Math.max(x, b.e), -1);
    m.summary = !real.length ? 'Nothing booked' : last < 0 ? 'All done' : `done ${hm(last)}`;
    m.label = isToday ? 'Later' : weekday(S.day);

    if (S.error && !S.loaded) Object.assign(m, { what: "Couldn't load today", when: 'Trying again in a minute' });
    else if (next) {
      const d = next.s - now;
      m.chip = d <= 0 ? 'now' : d < 60 ? `in ${d} min` : `in ${dur(d)}`;
      m.when = range(next.s, next.e);
      m.what = next.title;
    } else if (isToday) {
      // No hour here: the founder's working hours live in cal.com, and guessing "6 PM" was wrong for most people.
      m.when = 'Rest of today';
      m.what = real.length ? 'Nothing else booked' : 'No meetings';
    } else {
      m.when = real.length ? rangeAP(real[0].s, last) : 'Nothing booked';
      m.what = real.length ? `${real.length} meeting${real.length > 1 ? 's' : ''}` : 'No meetings';
    }

    // Free gaps of 30 min or more inside the visible day.
    m.free = [];
    let cur = isToday ? Math.max(now, lo) : lo;
    for (const b of real) {
      if (b.s - cur >= 30) m.free.push([cur, b.s]);
      cur = Math.max(cur, b.e);
    }
    if (hi - cur >= 30) m.free.push([cur, hi]);
    return m;
  }

  function renderPanel(anim = {}) {
    if (!S.loaded && !S.error) return;
    const panel = $('#panel');
    const prevLeft = {};
    panel.querySelectorAll('.blk[data-uid]').forEach(e => { prevLeft[e.dataset.uid] = e.style.left; });
    const m = model();
    const enter = anim.enter || new Set(), move = anim.move || new Map();
    const marked = v => ['pend', 'cand', 'sel', 'ghost'].includes(v);

    const body = h('div', { class: 'pbody' });
    if (anim.day) { body.classList.add('dayin'); body.style.setProperty('--dx', anim.day); }

    const head = h('div', { class: 'phead' },
      h('span', { class: m.isToday ? 'circle' : 'circle other' }, String(+S.day.slice(8))),
      h('h2', null, weekday(S.day)));
    if (m.isToday && m.left > 0) head.append(h('span', { class: 'left' }, h('b', null, String(m.left)), ' left today'));
    if (!m.isToday) head.append(h('button', { class: 'pill', type: 'button', onclick: () => loadSchedule(todayStr()) }, 'Today'));
    head.append(h('button', { class: 'pill new', type: 'button', onclick: openNew }, 'New'));
    head.append(acctButton()); // phone only; the desktop one sits in the header

    const nx = m.next;
    const next = h('div', { class: `next${nx && nx.v ? ' ' + nx.v : ''}`, 'data-uid': nx ? nx.uid : null },
      h('div', { class: 'meta' }, m.chip && h('span', { class: 'chipnow' }, m.chip), h('span', { class: 'when' }, m.when)),
      h('span', { class: 'what' }, m.what));
    if (nx && (enter.has(nx.uid) || (anim.appear && marked(nx.v)))) next.classList.add('appear');

    const toggle = h('button', { class: 'ptoggle', type: 'button', 'aria-expanded': String(S.phoneOpen) },
      h('span', null, ...(m.isToday
        ? [h('b', null, String(m.left)), ' left today · ', m.summary]
        : [h('b', null, String(m.items.filter(b => !b.virtual).length)), ' booked · ', m.summary])),
      h('span', { class: 'lbl', icon: undefined }, m.label));
    toggle.lastChild.insertAdjacentHTML('beforeend', chev('#050505'));
    toggle.onclick = () => { S.phoneOpen = !S.phoneOpen; renderPanel(); };

    // Strip: 9 to 6 unless a meeting falls outside.
    const pct = x => `${((x - m.lo) / (m.hi - m.lo) * 100).toFixed(2)}%`;
    const bar = h('div', { class: 'bar', role: 'img', 'aria-label': `Day from ${hm(m.lo)} ${ap(m.lo)} to ${hm(m.hi)} ${ap(m.hi)}, ${m.summary}` });
    if (m.isToday && m.now > m.lo) bar.append(h('div', { class: 'past', style: `width:${pct(Math.min(m.now, m.hi))}` }));
    const slides = [];
    for (const b of m.items) {
      const v = b.v || (m.past(b) ? 'past' : b === nx ? 'next' : 'ev');
      const over = m.items.filter(o => o !== b && o.s < b.e && b.s < o.e);
      const lane = over.length ? (over.some(o => o.s < b.s || (o.s === b.s && m.items.indexOf(o) < m.items.indexOf(b))) ? ' half-b' : ' half-a') : '';
      const el = h('div', { class: `blk v-${v}${lane}`, 'data-uid': b.uid });
      el.style.left = pct(Math.max(b.s, m.lo));
      el.style.width = pct(m.lo + Math.min(b.e, m.hi) - Math.max(b.s, m.lo));
      if (enter.has(b.uid)) el.classList.add('grow');
      if (anim.appear && marked(v)) el.classList.add('appear');
      if (move.has(b.uid) && prevLeft[move.get(b.uid)] && !reduced()) slides.push([el, prevLeft[move.get(b.uid)]]);
      bar.append(el);
    }
    if (m.isToday && m.now >= m.lo && m.now <= m.hi) bar.append(h('div', { class: 'now', style: `left:${pct(m.now)}` }));
    const ticks = h('div', { class: 'ticks' });
    for (let i = 0; i < 4; i++) ticks.append(h('span', null, String((Math.round((m.lo + i * (m.hi - m.lo) / 3) / 60) % 12) || 12)));

    const rows = h('ol', { class: 'rows' });
    for (const b of m.later) {
      const tag = S.tags[b.uid] || b.tag;
      const li = h('li', { class: `row${b.v ? ' v-' + b.v : ''}`, 'data-uid': b.uid },
        h('span', { class: 'tm' }, hm(b.s)), h('span', { class: 'ti' }, b.title), tag && h('span', { class: 'tag' }, tag));
      if (enter.has(b.uid)) li.classList.add('enter');
      else if (move.has(b.uid)) li.classList.add('flash');
      else if (anim.appear && marked(b.v)) li.classList.add('appear');
      rows.append(li);
    }
    const list = h('div', { class: 'listsec' },
      h('div', { class: 'listhead' }, h('span', { class: 'eb' }, `>> ${m.label}`), h('span', { class: 'sum' }, m.summary)),
      rows);
    if (m.free.length) {
      const btn = h('button', { class: 'more', type: 'button', 'aria-expanded': String(S.freeOpen) }, 'Free time');
      btn.insertAdjacentHTML('beforeend', chev('#050505'));
      btn.onclick = () => { S.freeOpen = !S.freeOpen; renderPanel(); };
      list.append(btn, h('div', { class: 'free', hidden: !S.freeOpen },
        m.free.map(([a, b]) => h('div', null, h('span', null, range(a, b)), h('span', null, dur(b - a))))));
    }

    body.append(head, next, toggle, h('div', { class: 'strip' }, bar, ticks), list);
    panel.replaceChildren(body);
    panel.classList.toggle('open', S.phoneOpen);
    for (const [el, from] of slides) {
      const to = el.style.left;
      el.style.left = from;
      el.getBoundingClientRect();
      el.classList.add('slide');
      el.style.left = to;
    }
  }

  let seq = 0;
  async function loadSchedule(day = S.day, mkAnim) {
    const my = ++seq;
    let data;
    try {
      data = await api(`/api/schedule?tz=${encodeURIComponent(TZ)}&day=${day}&days=1`);
    } catch (e) {
      if (my === seq && !S.loaded) { S.error = true; renderPanel(); }
      return;
    }
    if (my !== seq) return;
    const dayChanged = day !== S.day;
    const anim = mkAnim && !dayChanged ? mkAnim(S.bookings, data.bookings || []) : {};
    if (dayChanged && S.loaded) anim.day = day > S.day ? '8px' : '-8px';
    S.day = day; S.bookings = data.bookings || []; S.loaded = true; S.error = false;
    renderPanel(anim);
  }

  function animateOut(uid) {
    return new Promise(res => {
      const els = $('#panel').querySelectorAll(`[data-uid="${CSS.escape(uid)}"]`);
      if (!els.length || reduced()) return res();
      els.forEach(e => e.classList.add('out'));
      setTimeout(res, 200);
    });
  }

  // ---------- chat ----------
  const stack = $('#stack'), log = $('#log'), chat = $('#chat'), input = $('#ask'), sendBtn = $('#send');
  function push(el) {
    const intro = $('#intro');
    if (intro) { intro.remove(); syncSuggest(); }
    stack.append(el);
    requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; });
    return el;
  }
  const botLine = text => h('p', { class: 'bot' }, text);
  // Suggested actions above the input (desktop). Hidden, but still holding their space, while a
  // request is in flight or the first-run intro already offers starters, so the chat never jumps.
  const suggest = $('#suggest');
  function syncSuggest() {
    const off = S.busy || !!$('#intro');
    suggest.classList.toggle('off', off);
    suggest.inert = off;
  }
  suggest.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b || S.busy) return;
    if (b.dataset.act === 'book') openNew();
    else send(b.dataset.say);
  });

  const TIMED = /^(\d{1,2}:\d{2})(?:\s*[ap]\.?m\.?)?\s*[—–\-:·]?\s*(.+)$/i;
  function botBubble(reply) {
    const lines = String(reply || '').replace(/\*\*|__|`/g, '').split('\n').map(s => s.trim()).filter(Boolean);
    const b = h('div', { class: 'bot' });
    const rest = lines.slice(1).map(l => l.replace(/^[-*•]\s*/, ''));
    const listy = rest.length >= 2 && rest.filter(l => TIMED.test(l)).length >= 2;
    if (!listy) { lines.forEach(l => b.append(h('span', null, l))); return b; }
    b.classList.add('list');
    const items = h('div', { class: 'items', hidden: true }, rest.map(l => {
      const m = l.match(TIMED);
      return m ? h('div', null, h('span', { class: 't' }, m[1]), h('span', null, m[2])) : h('div', null, h('span', null, l));
    }));
    const label = `Show all ${rest.length}`;
    const btn = h('button', { class: 'more', type: 'button', 'aria-expanded': 'false' }, label);
    btn.onclick = () => {
      items.hidden = !items.hidden;
      btn.setAttribute('aria-expanded', String(!items.hidden));
      btn.textContent = items.hidden ? label : 'Hide';
    };
    b.append(h('span', null, lines[0]), items, btn);
    return b;
  }

  function addOptions(bubble, options) {
    const chipMode = options.every(o => String(o.label).length <= 10);
    const group = h('div', { class: chipMode ? 'chips' : 'opts', role: 'group' });
    const today = S.bookings.filter(b => !S.hidden.has(b.uid)).map(norm);
    for (const o of options) {
      const label = String(o.label);
      let btn;
      if (chipMode) btn = h('button', { class: 'chip', type: 'button' }, label);
      else {
        // A "which one?" option that names a meeting on the panel gets that meeting's time and length, and outlines it.
        const hit = today.find(b => b.title && label.toLowerCase().includes(b.title.toLowerCase()));
        if (hit) S.cands.add(hit.uid);
        btn = h('button', { class: 'opt', type: 'button' }, ...(hit
          ? [h('span', { class: 't' }, hm(hit.s)), h('span', { class: 'grow' }, hit.title), h('span', { class: 'dur' }, dur(hit.e - hit.s))]
          : [h('span', { class: 'grow' }, label)]));
      }
      btn.onclick = () => {
        if (S.busy) return;
        group.querySelectorAll('button').forEach(x => {
          x.disabled = true;
          x.classList.add(x === btn ? 'on' : 'off');
          if (x === btn) x.setAttribute('aria-pressed', 'true');
        });
        send(String(o.value), label);
      };
      group.append(btn);
    }
    if (!chipMode) bubble.classList.add('wide');
    bubble.append(group);
  }

  const cardEnd = (start, c) => minOf(start) + (+c.minutes || 30);
  function cardWhen(start, c, withDay) {
    const s = minOf(start), e = cardEnd(start, c);
    return withDay ? `${dayLabel(start.slice(0, 10))} · ${rangeAP(s, e)}` : range(s, e);
  }

  function addCard(c) {
    S.cards.set(c.change_id, c);
    const el = h('article', { class: 'card', 'data-cid': c.change_id,
      'aria-label': { book: 'New meeting', reschedule: 'Move meeting', cancel: 'Cancel meeting' }[c.kind] || 'Change' });
    const title = h('span', { class: 'title' }, c.title);
    let detail = null;
    if (c.kind === 'reschedule') {
      // Same day: say the day once under the title and keep each box to just its times, so neither wraps.
      const day = c.new_start.slice(0, 10);
      const sameDay = c.old_start.slice(0, 10) === day;
      const lbl = s => (sameDay ? '' : `${dayLabel(s.slice(0, 10))} · `) + range(minOf(s), cardEnd(s, c));
      el.append(h('div', { class: 'head' }, title, sameDay ? h('span', { class: 'sub' }, dayLabel(day)) : null), h('div', { class: 'swap' },
        h('div', { class: 'old' }, h('span', { class: 't' }, lbl(c.old_start))),
        icon(SWAP),
        h('div', { class: 'new' }, h('span', { class: 't' }, lbl(c.new_start)))));
      if (c.attendee) detail = `${c.attendee} gets the new time.`;
    } else {
      const start = c.kind === 'cancel' ? c.old_start : c.new_start;
      let sub = cardWhen(start, c, true);
      if (c.kind === 'cancel' && c.attendee) sub += ` · ${String(c.attendee).split(' ')[0]} will be told`;
      el.append(h('div', { class: 'head' }, title, h('span', { class: 'sub' }, sub)));
    }
    const primary = c.kind === 'cancel'
      ? h('button', { class: 'ink', type: 'button' }, 'Cancel it')
      : h('button', { class: 'cta', type: 'button' }, icon(ARROW), c.kind === 'book' ? 'Book' : 'Move');
    const secondary = h('button', { class: 'ghost', type: 'button' }, c.kind === 'cancel' ? 'Keep' : 'Change');
    const acts = h('div', { class: 'acts' }, primary, secondary);
    if (detail) {
      const d = h('span', { class: 'detail', hidden: true }, detail);
      const more = h('button', { class: 'link', type: 'button', 'aria-expanded': 'false' }, 'Details');
      more.insertAdjacentHTML('beforeend', chev('#6B6B6B'));
      more.onclick = () => { d.hidden = !d.hidden; more.setAttribute('aria-expanded', String(!d.hidden)); };
      acts.append(more);
      el.append(d);
    }
    if (c.kind === 'book') el.append(availabilityNote(c));
    el.append(acts);
    primary.onclick = () => confirmCard(c, el);
    secondary.onclick = () => { S.cards.delete(c.change_id); el.remove(); renderPanel(); input.focus(); };
    push(el);
  }

  // cal.com checks only the founder's calendar. Say so in one quiet line; the rest shows on hover, focus or tap.
  // The email sits in the reveal so a wrong address can be caught before the invite goes out.
  function availabilityNote(c) {
    const who = c.attendee || 'They';
    const more = h('span', { class: 'more-note', id: `note-${c.change_id}` },
      `${who} gets an email invite${c.email ? ` at ${c.email}` : ''}. Make sure the time works for them.`);
    const btn = h('button', { class: 'note', type: 'button', 'aria-expanded': 'false', 'aria-controls': `note-${c.change_id}` },
      icon(INFO), 'Only your calendar was checked');
    btn.onclick = () => btn.setAttribute('aria-expanded', String(btn.getAttribute('aria-expanded') !== 'true'));
    return h('div', { class: 'avail' }, btn, more);
  }

  function receipt(word, c, start) {
    const s = minOf(start), e = cardEnd(start, c);
    const when = (dayLabel(start.slice(0, 10)) === 'Today' ? '' : `${dayLabel(start.slice(0, 10))} · `) + range(s, e);
    return h('div', { class: 'done', role: 'status' }, icon(CHECK, 'ok'), h('span', null, `${word} · ${c.title}`), h('span', { class: 't when' }, when));
  }

  async function confirmCard(c, el) {
    el.querySelectorAll('button').forEach(b => { b.disabled = true; });
    if (used.has(c.change_id) || !S.cards.has(c.change_id)) return;
    if (c.kind === 'cancel') return holdCancel(c, el);
    S.cards.delete(c.change_id); // no longer open, so a message sent meanwhile won't drop it
    const req = sendCard(c);
    if (!req) return;
    let r;
    try {
      r = await req;
    } catch (e) {
      // The write may have gone through, so the card stays spent rather than offering a second run.
      if (errText(e)) push(botLine(errText(e)));
      return;
    }
    applyConfirm(r, c, el);
  }

  function applyConfirm(r, c, el) {
    const before = matchBooking(c, S.bookings.filter(b => !S.hidden.has(b.uid)).map(norm));
    S.cards.delete(c.change_id);
    if (r.ok && c.kind === 'cancel') {
      const rec = h('div', { class: 'done', role: 'status' }, h('span', { class: 'gone' }, c.title), h('span', { class: 'when' }, 'Cancelled'));
      if (el) el.replaceWith(rec); else push(rec);
      if (before) { S.hidden.add(before.uid); renderPanel(); }
      loadSchedule();
    } else if (r.ok) {
      const moved = c.kind === 'reschedule';
      const rec = receipt(moved ? 'Moved' : 'Booked', c, c.new_start);
      if (el) el.replaceWith(rec); else push(rec);
      loadSchedule(c.new_start.slice(0, 10), (prev, next) => {
        const known = new Set(prev.map(b => b.uid));
        const landed = next.find(b => b.start === c.new_start && b.title === c.title) || next.find(b => b.start === c.new_start);
        if (moved) {
          const map = new Map();
          if (landed && before) { map.set(landed.uid, before.uid); S.tags[landed.uid] = 'Moved'; }
          return { move: map };
        }
        const enter = new Set(next.filter(b => !known.has(b.uid)).map(b => b.uid));
        if (landed) S.tags[landed.uid] = 'New';
        return { enter };
      });
    } else {
      if (el) {
        el.querySelector('.acts').remove();
        el.querySelectorAll('.detail, .avail').forEach(x => x.remove());
        const sub = el.querySelector('.sub, .swap .new'); if (sub) sub.classList.add('struck');
        el.append(h('div', { class: 'alert enter', role: 'alert' }, h('span', null, icon(ALERT), r.message || "That didn't go through.")));
      } else push(botLine(r.message || "That didn't go through."));
      renderPanel();
      loadSchedule(S.day, (prev, next) => {
        const known = new Set(prev.map(b => b.uid));
        return { enter: new Set(next.filter(b => !known.has(b.uid)).map(b => b.uid)) };
      });
    }
    if (r.followup) renderResponse(r.followup);
  }

  // ---------- cancel with undo ----------
  // "Cancel it" hides the row and shows the toast; /api/confirm is only called once the toast runs out.
  let toast = null;
  function closeToast() {
    if (!toast) return;
    const { el, timer } = toast;
    clearTimeout(timer);
    toast = null;
    chat.classList.remove('has-toast');
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 200);
  }

  function holdCancel(c, el) {
    const before = matchBooking(c, S.bookings.filter(b => !S.hidden.has(b.uid)).map(norm));
    const uid = before && before.uid;
    S.cards.delete(c.change_id);
    let rec = h('div', { class: 'done', role: 'status' }, h('span', { class: 'gone' }, c.title), h('span', { class: 'when' }, 'Cancelled'));
    el.replaceWith(rec);
    if (uid) animateOut(uid).then(() => { S.hidden.add(uid); renderPanel(); });
    else renderPanel();

    const restore = (tag, line) => {
      if (uid) {
        S.hidden.delete(uid);
        if (tag) S.tags[uid] = tag;
        renderPanel({ enter: new Set([uid]) });
      }
      const back = tag ? receipt(tag, c, c.old_start) : h('div', { class: 'done', role: 'status' }, h('span', null, c.title), h('span', { class: 'when' }, 'Not cancelled'));
      rec.replaceWith(back);
      rec = back;
      if (line) push(botLine(line));
    };
    const commit = async () => {
      closeToast();
      pendingCancel = null;
      let r;
      const req = sendCard(c);
      if (!req) return;
      try { r = await req; } catch (e) {
        // No answer (network drop, or cal.com timed out): the cancel may have gone through, so don't claim it didn't.
        if (e.status === undefined || e.status === 503) {
          push(botLine("Couldn't confirm the cancel. Check your calendar before trying again."));
          loadSchedule();
          return;
        }
        r = { ok: false, message: errText(e) };
      }
      if (!r.ok) restore(null, r.message || "That didn't go through.");
      loadSchedule();
      if (r.followup) renderResponse(r.followup);
    };

    closeToast();
    if (pendingCancel) pendingCancel();
    const undo = h('button', { type: 'button' }, 'Undo');
    const bar = h('i', { class: 'count' });
    bar.style.animationDuration = `${UNDO_SECONDS}s`;
    const t = h('div', { class: 'toast', role: 'status' }, h('span', null, 'Meeting cancelled'), undo, bar);
    chat.append(t);
    chat.classList.add('has-toast');
    toast = { el: t, timer: setTimeout(commit, UNDO_SECONDS * 1000) };
    pendingCancel = commit;
    undo.onclick = () => { closeToast(); pendingCancel = null; restore('Restored'); };
  }
  let pendingCancel = null; // sends a held cancel now, so a new message can't expire its card on the server

  // ---------- quick-fill booking form ----------
  const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const SLOTS_SHOWN = 8;
  function nextDays() {
    const d = toDate(todayStr()), out = [ymd(d)];
    d.setDate(d.getDate() + 1); out.push(ymd(d));
    while (out.length < 7) { d.setDate(d.getDate() + 1); if (d.getDay() % 6) out.push(ymd(d)); }
    return out;
  }
  function chipDay(s) {
    const l = dayLabel(s);
    if (l === 'Today' || l === 'Tomorrow') return l;
    const d = toDate(s);
    return `${d.toLocaleDateString('en-US', { weekday: 'short' })} ${d.getDate()}`;
  }

  let opening = false;
  async function openNew() {
    const open = stack.querySelector('.card.form');
    if (open) { open.scrollIntoView({ block: 'nearest' }); return; }
    if (opening) return;
    opening = true;
    try { addForm(await api(`/api/booking-form?tz=${encodeURIComponent(TZ)}`)); }
    catch (e) { if (errText(e)) push(botLine(errText(e))); }
    opening = false;
  }

  function addForm(f) {
    const pre = f.prefill || {};
    const types = f.event_types || [];
    const st = {
      type: (types.find(t => t.id === pre.event_type_id) || types[0] || {}).id,
      day: f.day || pre.day || todayStr(), slots: f.slots || [], slot: null,
      openDays: Array.isArray(f.open_days) ? new Set(f.open_days) : null, // null: server didn't say, treat all as open
      other: false, more: false, loading: false, err: '', seq: 0, sent: false,
    };
    const isOpen = d => !st.openDays || st.openDays.has(d);
    const chip = (label, on, onclick, title, cls = '') => h('button', { class: `chip${on ? ' on' : ''}${cls ? ' ' + cls : ''}`, type: 'button', 'aria-pressed': String(on), title, onclick }, label);
    const sec = (label, ...kids) => h('div', { class: 'sec' }, h('span', { class: 'lab' }, label), ...kids);

    const typeRow = h('div', { class: 'chips' });
    const drawTypes = () => typeRow.replaceChildren(...types.map(t =>
      chip(t.title, t.id === st.type, () => { if (st.type === t.id) return; st.type = t.id; drawTypes(); refetch(); }, `${t.minutes} min`)));

    const name = h('input', { type: 'text', autocomplete: 'name', placeholder: 'Name', 'aria-label': 'Name', maxlength: '200' });
    const email = h('input', { type: 'email', autocomplete: 'email', placeholder: 'Email', 'aria-label': 'Email', maxlength: '200' });
    name.value = pre.name || '';
    email.value = pre.email || '';
    const hint = h('span', { class: 'hint', hidden: true }, 'Check the email address.');
    const people = h('div', { class: 'chips' });
    const drawPeople = () => people.replaceChildren(...(f.contacts || []).map(c =>
      chip(c.name || c.email, name.value === (c.name || '') && email.value === c.email, () => {
        const picked = name.value === (c.name || '') && email.value === c.email;
        name.value = picked ? '' : c.name || ''; // tapping the selected person again clears them
        email.value = picked ? '' : c.email || '';
        hint.hidden = true; drawPeople(); update();
      }, c.email)));

    const days = nextDays();
    if (!days.includes(st.day)) days.push(st.day);
    const dayRow = h('div', { class: 'chips' });
    const pickDay = d => {
      if (st.day === d) return;
      st.day = d; st.slot = null; st.more = false;
      drawDays();
      if (isOpen(d)) refetch(); else { drawSlots(); update(); }
    };
    const drawDays = () => dayRow.replaceChildren(...days.map(d =>
      chip(chipDay(d), d === st.day, () => pickDay(d), null, isOpen(d) ? '' : 'muted')));

    const time = h('input', { type: 'time', 'aria-label': 'Other time', step: '300' });
    time.addEventListener('input', update);
    const slotRow = h('div', { class: 'chips' });
    function drawSlots() {
      const kids = [];
      if (st.loading) kids.push(h('span', { class: 'hint' }, 'Finding open times…'));
      else if (st.err) kids.push(h('span', { class: 'hint' }, st.err));
      else if (!isOpen(st.day) || !st.slots.length) {
        // A full day: say so once and offer the next free day, nothing else.
        const l = dayLabel(st.day);
        kids.push(h('span', { class: 'hint' }, `You're not free ${l === 'Today' || l === 'Tomorrow' ? l.toLowerCase()
          : 'on ' + toDate(st.day).toLocaleDateString('en-US', { weekday: 'short' })}.`));
        const next = st.openDays && [...st.openDays].sort().find(d => d > st.day);
        if (next) kids.push(chip(`${chipDay(next)} →`, false, () => { if (!days.includes(next)) days.push(next); pickDay(next); }, null, 'jump'));
        slotRow.replaceChildren(...kids);
        time.hidden = true;
        st.other = false;
        return;
      } else {
        const shown = st.more ? st.slots : st.slots.slice(0, SLOTS_SHOWN);
        for (const sl of shown) kids.push(chip(hm(minOf(sl)), !st.other && st.slot === sl, () => { st.slot = sl; st.other = false; drawSlots(); update(); }));
        if (!st.more && st.slots.length > SLOTS_SHOWN) kids.push(chip(`+${st.slots.length - SLOTS_SHOWN} more`, false, () => { st.more = true; drawSlots(); }));
      }
      kids.push(chip('Other time', st.other, () => { st.other = !st.other; st.slot = null; drawSlots(); update(); if (st.other) time.focus(); }));
      slotRow.replaceChildren(...kids);
      time.hidden = !st.other;
    }
    async function refetch() {
      const my = ++st.seq;
      Object.assign(st, { loading: true, err: '', slot: null, more: false });
      drawSlots(); update();
      try {
        const r = await api(`/api/booking-form?tz=${encodeURIComponent(TZ)}&day=${st.day}&event_type_id=${encodeURIComponent(st.type)}`);
        if (my === st.seq) {
          if (Array.isArray(r.open_days)) st.openDays = new Set(r.open_days);
          st.slots = r.day === st.day || !r.day ? r.slots || [] : []; // the server moved off a full day; keep the user's pick
          drawDays();
        }
      } catch (e) {
        if (my === st.seq) { st.slots = []; st.err = errText(e) || ERR; }
      }
      if (my === st.seq) { st.loading = false; drawSlots(); update(); }
    }

    const start = () => (st.other ? (time.value ? `${st.day}T${time.value.slice(0, 5)}` : null) : st.slot);
    const review = h('button', { class: 'cta', type: 'button', disabled: true }, icon(ARROW), 'Review');
    function update() {
      const ok = st.type != null && name.value.trim() && EMAIL.test(email.value.trim()) && start();
      review.disabled = !ok || st.sent;
    }
    name.addEventListener('input', () => { drawPeople(); update(); });
    email.addEventListener('input', () => { hint.hidden = true; drawPeople(); update(); });
    email.addEventListener('blur', () => { hint.hidden = !email.value.trim() || EMAIL.test(email.value.trim()); });

    const close = h('button', { class: 'x', type: 'button', 'aria-label': 'Close', icon: CLOSE, onclick: () => el.remove() });
    const el = h('article', { class: 'card form', 'aria-label': 'New meeting' },
      h('div', { class: 'fhead' }, h('span', { class: 'title' }, 'New meeting'), close),
      sec('Type', typeRow),
      sec('Who', people, h('div', { class: 'fields' }, name, email), hint),
      sec('When', dayRow, slotRow, time),
      h('div', { class: 'acts' }, review));

    review.onclick = async () => {
      if (st.sent || review.disabled) return;
      st.sent = true; // a form card submits once
      el.querySelectorAll('button, input').forEach(x => { x.disabled = true; });
      const body = { event_type_id: st.type, start: start(), name: name.value.trim(), email: email.value.trim() };
      let r;
      // Propose writes nothing, so a failed one can simply be tried again from the same card.
      const reopen = msg => {
        st.sent = false;
        el.querySelectorAll('button, input').forEach(x => { x.disabled = false; });
        update();
        if (msg) push(botLine(msg));
      };
      try { r = await api('/api/propose', () => ({ ...body, history })); } catch (e) { reopen(errText(e)); return; }
      keepHistory(r);
      if (!r.ok) { reopen(r.message || "That didn't go through. Try again."); return; }
      const s = minOf(body.start);
      el.replaceWith(h('div', { class: 'done', role: 'status' }, h('span', null, `New meeting · ${body.name}`),
        h('span', { class: 't when' }, `${chipDay(body.start.slice(0, 10))} · ${hm(s)} ${ap(s)}`)));
      renderResponse({ reply: '', cards: r.cards || [], options: [] });
    };

    drawTypes(); drawPeople(); drawDays(); drawSlots(); update();
    push(el);
    // On a phone the card is taller than the chat; show its top (Type) rather than its bottom.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (el.offsetHeight > log.clientHeight) el.scrollIntoView({ block: 'start' });
    }));
    if (!f.slots && st.type != null) refetch();
  }

  // ---------- voice input ----------
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = $('#mic');
  let stopMic = () => {};
  if (!SR) mic.remove();
  else {
    let rec = null;
    // Sending ends dictation, so a late result can't refill the box with what was just sent.
    stopMic = () => { if (rec) { const r = rec; rec = null; setRec(false); r.abort(); } };
    const setRec = on => { mic.setAttribute('aria-pressed', String(on)); };
    mic.addEventListener('click', () => {
      if (rec) { rec.stop(); return; }
      const r = new SR();
      r.interimResults = true;
      r.lang = navigator.language;
      const base = input.value.trim();
      r.onresult = e => {
        let text = '', final = false;
        for (let i = 0; i < e.results.length; i++) { text += e.results[i][0].transcript; if (e.results[i].isFinal) final = true; }
        input.value = ((base ? base + ' ' : '') + text.trim()).slice(0, 2000);
        if (final) r.stop();
      };
      r.onerror = e => {
        if (e.error === 'not-allowed' || e.error === 'service-not-allowed') push(botLine('Mic blocked. Allow it in your browser to talk.'));
      };
      r.onend = () => { if (rec === r) { rec = null; setRec(false); input.focus(); } };
      rec = r;
      setRec(true);
      try { r.start(); } catch (e) { rec = null; setRec(false); }
    });
  }

  // ---------- send / render a chat response ----------
  function renderResponse(r) {
    if (r.confirmed) {
      const c = r.confirmed.card || (S.cards.size === 1 ? [...S.cards.values()][0] : null);
      const el = c && stack.querySelector(`[data-cid="${CSS.escape(c.change_id)}"]`);
      if (c) { used.add(c.change_id); applyConfirm(r.confirmed, S.cards.get(c.change_id) || c, el); }
      else push(botLine(r.confirmed.message || r.reply || ''));
      return;
    }
    const opts = r.options || [], cards = r.cards || [];
    if ((r.reply && r.reply.trim()) || opts.length) {
      const b = botBubble(r.reply);
      if (opts.length) addOptions(b, opts);
      push(b);
    }
    if (r.form) addForm(r.form);
    cards.forEach(addCard);
    const last = cards[cards.length - 1];
    const day = (r.view && r.view.start_date) || (last && (last.kind === 'cancel' ? last.old_start : last.new_start) || '').slice(0, 10);
    const appear = cards.length > 0 || S.cands.size > 0;
    if (day && day !== S.day) loadSchedule(day);
    else renderPanel({ appear });
  }

  async function send(value, shown) {
    const text = String(value).trim();
    if (!text || S.busy) return;
    S.busy = true; sendBtn.disabled = true; syncSuggest();
    if (pendingCancel) await pendingCancel();
    S.tags = {}; S.cands.clear();
    push(h('p', { class: 'me' }, shown || text));
    const open = [...S.cards.keys()];
    const openCards = [...S.cards.values()].filter(c => !used.has(c.change_id));
    // A typed "yes" to a single cancel card runs nothing (the server asks for the tap), so that card stays open.
    const keepOpen = YES.test(text) && openCards.length === 1 && openCards[0].kind === 'cancel';
    if (YES.test(text) && openCards.length === 1 && !keepOpen) {
      // The typed yes confirms this card on the server, so it's spent now; its button must not send it again.
      const only = openCards[0];
      used.add(only.change_id);
      const el = stack.querySelector(`[data-cid="${CSS.escape(only.change_id)}"]`);
      if (el) el.querySelectorAll('button').forEach(b => { b.disabled = true; });
    }
    renderPanel();
    const dots = push(h('div', { class: 'thinking', role: 'status', 'aria-label': 'Assistant is working' },
      h('span', { class: 'dot' }), h('span', { class: 'dot' }), h('span', { class: 'dot' })));
    let r = null, err = null;
    try { r = await api('/api/chat', () => ({ message: text, history, open_cards: openCards })); } catch (e) { err = e; }
    keepHistory(r);
    dots.remove();
    S.busy = false; sendBtn.disabled = false; syncSuggest();
    if (!r) { if (errText(err)) push(botLine(errText(err))); return; }
    if (!r.confirmed && !keepOpen) {
      // A new request drops any open card on the server; grey them out here too.
      for (const id of open) {
        S.cards.delete(id);
        const el = stack.querySelector(`[data-cid="${CSS.escape(id)}"]`);
        if (el) { el.classList.add('stale'); el.querySelectorAll('button').forEach(b => { b.disabled = true; }); }
      }
    }
    renderResponse(r);
  }

  $('#form').addEventListener('submit', e => {
    e.preventDefault();
    if (S.busy) return;
    stopMic();
    const v = input.value;
    input.value = '';
    send(v);
  });
  stack.addEventListener('click', e => {
    const b = e.target.closest('[data-say]');
    if (b) send(b.dataset.say);
  });

  document.querySelector('.top .acct').addEventListener('click', toggleMenu);
  document.addEventListener('click', e => {
    // A tapped-open availability note closes on the next tap anywhere else.
    document.querySelectorAll('.note[aria-expanded="true"]').forEach(n => { if (!n.contains(e.target)) n.setAttribute('aria-expanded', 'false'); });
    if (!$('#menu').hidden && !e.target.closest('#menu')) {
      $('#menu').hidden = true;
      document.querySelectorAll('.acct').forEach(b => b.setAttribute('aria-expanded', 'false'));
    }
  });
  $('#signout').addEventListener('click', async () => {
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (e) { /* reload shows the real state */ }
    location.reload();
  });

  (async () => {
    let me = null;
    try { me = await api('/api/me'); } catch (e) { if (!$('#connect').hidden) return; }
    if (me && !me.connected) return showConnect();
    S.name = (me && me.name) || '';
    $('.top .acct').textContent = initials(S.name);
    $('#who').textContent = S.name;
    $('#app').hidden = false;
    loadSchedule(S.day);
    setInterval(() => {
      if ($('#app').hidden) return;
      renderPanel(); // the next-up chip updates from local data every minute
      if (!queued) loadSchedule(); // skip the poll while another call is in flight
    }, 60000);
  })();
})();
