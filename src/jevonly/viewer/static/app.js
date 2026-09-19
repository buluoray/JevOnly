(() => {
  const $ = (id) => document.getElementById(id);
  const feed = $('feed'),
    shot = $('shot'),
    badge = $('badge'),
    phaseText = $('phase-text');
  let es = null,
    fs = null,
    cards = {},
    currentStep = null,
    startedAt = null,
    timer = null,
    thresholds = {},
    lastPlan = null,
    endSeen = false;
  let live = false,
    fpsWindow = [];
  let LOG = [],
    runMeta = {};

  $('check-kind').addEventListener('change', (e) => {
    $('check-val').disabled = e.target.value === 'none';
    if (!$('check-val').disabled) $('check-val').focus();
  });

  // Remember the form across reloads in localStorage. The API key is never stored: it lives only in
  // its input for the life of the tab and is sent once per run in the /run body.
  const FORM_FIELDS = [
    'goal',
    'start',
    'facts',
    'steps',
    'budget',
    'cap',
    'check-kind',
    'check-val',
    't-verify',
    't-offpath',
    't-done',
    't-risk',
    'irreversible',
  ];
  const FORM_CHECKS = ['kb', 'vp', 'headed'];
  const saveForm = () => {
    try {
      const v = {};
      for (const id of FORM_FIELDS) v[id] = $(id).value;
      for (const id of FORM_CHECKS) v[id] = $(id).checked;
      localStorage.setItem('jevonly.form', JSON.stringify(v));
    } catch (e) {
      /* storage unavailable: nothing to remember */
    }
  };
  (function restoreForm() {
    try {
      const v = JSON.parse(localStorage.getItem('jevonly.form') || 'null');
      if (v) {
        for (const id of FORM_FIELDS) if (typeof v[id] === 'string') $(id).value = v[id];
        for (const id of FORM_CHECKS) if (typeof v[id] === 'boolean') $(id).checked = v[id];
        $('check-val').disabled = $('check-kind').value === 'none';
      }
    } catch (e) {
      /* ignore */
    }
  })();
  for (const id of [...FORM_FIELDS, ...FORM_CHECKS]) $(id).addEventListener('change', saveForm);
  // the sliders show their value beside the label; a run reads them when it starts
  for (const k of ['verify', 'offpath', 'done', 'risk']) {
    const show = () => ($('t-' + k + '-v').textContent = Number($('t-' + k).value).toFixed(2));
    $('t-' + k).addEventListener('input', show);
    show();
  }
  // Irreversible action waiting on the operator: the loop is parked until one of these is pressed.
  const approval = $('approval');
  const answerApproval = (decision) => {
    approval.hidden = true;
    fetch('/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    }).catch(() => null);
  };
  $('approve-allow').addEventListener('click', () => answerApproval('allow'));
  $('approve-deny').addEventListener('click', () => answerApproval('deny'));
  for (const id of ['goal', 'start', 'facts', 'check-val', 'key']) $(id).addEventListener('input', saveForm);

  // Facts editor: name/value rows over the hidden #facts field, which holds the pairs as JSON so
  // the form cache above keeps working unchanged. A cache written by the old textarea (one
  // `key = value` per line) is read as well.
  const factRows = $('fact-rows');
  const readFacts = () => {
    const t = $('facts').value.trim();
    if (!t) return [];
    try {
      const j = JSON.parse(t);
      if (Array.isArray(j)) return j.filter(Array.isArray).map(([k, v]) => [String(k ?? ''), String(v ?? '')]);
      if (j && typeof j === 'object') return Object.entries(j).map(([k, v]) => [k, String(v)]);
    } catch (e) {
      /* not JSON: the old line format */
    }
    return t
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'))
      .map((l) => {
        const m = l.match(/^([^=:]+)[=:](.*)$/);
        return m ? [m[1].trim(), m[2].trim()] : [l, ''];
      });
  };
  const factPairs = () =>
    [...factRows.querySelectorAll('.fact-row')]
      .map((r) => [r.children[0].value.trim(), r.children[1].value])
      .filter(([k, v]) => k || v);
  const syncFacts = () => {
    $('facts').value = JSON.stringify(factPairs());
    saveForm();
  };
  const addFactRow = (k = '', v = '', focus = false) => {
    const row = document.createElement('div');
    row.className = 'fact-row';
    row.innerHTML =
      '<input type="text" class="mono fact-k" placeholder="name" spellcheck="false" aria-label="fact name">' +
      '<input type="text" class="fact-v" placeholder="text to type" aria-label="fact value">' +
      '<button type="button" class="fact-x" title="Remove this fact" aria-label="Remove this fact">×</button>';
    const [kIn, vIn, x] = row.children;
    kIn.value = k;
    vIn.value = v;
    kIn.addEventListener('input', syncFacts);
    vIn.addEventListener('input', syncFacts);
    vIn.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (row === factRows.lastElementChild) addFactRow('', '', true);
      else row.nextElementSibling.children[0].focus();
    });
    x.addEventListener('click', () => {
      if (factRows.children.length > 1) row.remove();
      else {
        kIn.value = '';
        vIn.value = '';
      }
      syncFacts();
    });
    factRows.appendChild(row);
    if (focus) kIn.focus();
  };
  const renderFacts = () => {
    factRows.innerHTML = '';
    const pairs = readFacts();
    if (!pairs.length) pairs.push(['', '']);
    for (const [k, v] of pairs) addFactRow(k, v);
  };
  $('fact-add').addEventListener('click', () => addFactRow('', '', true));
  renderFacts();

  const fmt = (x, d = 2) => (x === null || x === undefined ? '–' : Number(x).toFixed(d));
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const img = (b64) => (b64 ? `data:image/jpeg;base64,${b64}` : null);

  function setStatus(cls, text) {
    const p = $('status');
    p.className = 'pill ' + cls;
    p.textContent = text;
  }
  function setPhase(cls, text) {
    badge.className = 'badge ' + cls;
    badge.textContent = cls || 'idle';
    phaseText.textContent = text;
  }
  // The stage is one fixed-size canvas. Swapping an <img>'s src for every frame re-laid the element out
  // on each decode (the box collapses until the new bytes are decoded), and at the 60 fps bursts the
  // screencast sends during animations that read as the picture zooming in and out. A canvas keeps its
  // box; frames are decoded off the main thread and only the newest pending one is painted.
  // The frame size the child streams (its viewport) times the display's pixel ratio, capped at 2: a
  // Retina screen shows the browser at native sharpness, a plain one is not asked to decode more than it
  // can show.
  const DPR = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  const STAGE_W = 1280 * DPR,
    STAGE_H = 900 * DPR;
  function stageCanvas() {
    let cv = shot.querySelector('canvas.stage');
    if (!cv) {
      shot.innerHTML = `<canvas class="stage" width="${STAGE_W}" height="${STAGE_H}" aria-label="browser viewport"></canvas><div class="live" id="live"><span class="dot"></span>LIVE <span id="fps"></span></div>`;
      cv = shot.querySelector('canvas.stage');
    }
    return cv;
  }
  let pendingB64 = null,
    painting = false,
    stageGen = 0; // bumped by Clear: a frame still decoding from before it must not repaint the stage
  function paintB64(b64) {
    pendingB64 = b64;
    if (painting) return; // the newest frame wins; older pending ones are dropped
    painting = true;
    const gen = stageGen;
    requestAnimationFrame(async () => {
      const cur = pendingB64;
      pendingB64 = null;
      try {
        const bytes = Uint8Array.from(atob(cur), (c) => c.charCodeAt(0));
        const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' }));
        if (gen !== stageGen) {
          bmp.close();
          throw new Error('stale frame');
        }
        const cv = stageCanvas(),
          ctx = cv.getContext('2d');
        // letterbox anything that is not the stage's own aspect (step screenshots are 1280x900, same ratio)
        const s = Math.min(STAGE_W / bmp.width, STAGE_H / bmp.height),
          w = Math.round(bmp.width * s),
          h = Math.round(bmp.height * s);
        if (w !== STAGE_W || h !== STAGE_H) {
          ctx.fillStyle = '#000';
          ctx.fillRect(0, 0, STAGE_W, STAGE_H);
        }
        ctx.drawImage(bmp, (STAGE_W - w) / 2, (STAGE_H - h) / 2, w, h);
        bmp.close();
      } catch (e) {
        /* a bad frame is skipped */
      }
      painting = false;
      if (pendingB64) paintB64(pendingB64);
    });
  }
  const stageImg = () => ({
    set src(v) {
      const m = /^data:image\/jpeg;base64,(.*)$/.exec(v || '');
      if (m) paintB64(m[1]);
    },
  }); // thumbnails click through here
  function showShot(b64, url) {
    if (b64 && !live) paintB64(b64);
    if (url) $('url').textContent = url;
  }
  // Every frame the screencast delivers is kept for the replay, stamped with its arrival time, so the
  // replay shows the run as it happened and not only the decision-point screenshots. Identical
  // consecutive frames are not stored twice. The store is bounded by bytes (frames are ~100-250 KB at
  // this quality): past MAX_FRAME_BYTES every second frame of the recording so far is dropped, so the
  // run keeps its full duration at half the rate.
  const FRAMES = [],
    MAX_FRAME_BYTES = 400 * 1024 * 1024;
  let frameBytes = 0,
    lastFrameB64 = null;
  function recordFrame(b64) {
    if (!b64 || b64 === lastFrameB64) return;
    lastFrameB64 = b64;
    FRAMES.push({ b64, t: Date.now() });
    frameBytes += b64.length;
    if (frameBytes > MAX_FRAME_BYTES) {
      for (let i = FRAMES.length - 1; i > 0; i -= 2) {
        frameBytes -= FRAMES[i].b64.length;
        FRAMES.splice(i, 1);
      }
    }
  }
  function onFrame(f) {
    live = true;
    const now = Date.now();
    fpsWindow.push(now);
    while (fpsWindow.length && now - fpsWindow[0] > 3000) fpsWindow.shift();
    recordFrame(f.jpeg_b64);
    paintB64(f.jpeg_b64);
    const l = $('live');
    if (l) {
      l.classList.add('on');
      $('fps').textContent = (fpsWindow.length / 3).toFixed(1) + ' fps';
    }
  }
  function connectFrames() {
    if (fs) fs.close();
    fs = new EventSource('/frames');
    fs.addEventListener('frame', (ev) => {
      try {
        onFrame(JSON.parse(ev.data));
      } catch (e) {
        console.error(e);
      }
    });
  }
  function stopFrames() {
    if (fs) {
      fs.close();
      fs = null;
    }
    live = false;
    const l = $('live');
    if (l) l.classList.remove('on');
  }
  function tick() {
    if (startedAt) $('s-el').textContent = Math.round((Date.now() - startedAt) / 1000) + 's';
  }

  // Feed scrolling: while following, an easing loop moves scrollTop a quarter of the remaining distance
  // per animation frame, so the column glides to the newest row and keeps gliding as rows land -- no
  // per-row jumps, no smooth-scroll animation fighting the next one. Following stops only when the
  // reader deliberately scrolls up (wheel, or a scroll that moves away from the bottom while nothing
  // programmatic is running) and resumes when they reach the bottom again or click the pill.
  const right = $('right'),
    newpill = $('newpill');
  let followFeed = true,
    following = false,
    lastTop = 0;
  right.addEventListener(
    'wheel',
    (e) => {
      if (e.deltaY < 0 && followFeed) {
        followFeed = false;
        newpill.classList.add('on');
      }
    },
    { passive: true },
  );
  right.addEventListener('scroll', () => {
    const atBottom = right.scrollHeight - right.scrollTop - right.clientHeight < 4;
    if (atBottom) {
      if (!followFeed) {
        followFeed = true;
        newpill.classList.remove('on');
      }
    } else if (!following && right.scrollTop < lastTop - 2 && followFeed) {
      followFeed = false;
      newpill.classList.add('on');
    }
    lastTop = right.scrollTop;
  });
  newpill.addEventListener('click', () => {
    followFeed = true;
    newpill.classList.remove('on');
    feedChanged();
  });
  function followTick() {
    if (!followFeed) {
      following = false;
      return;
    }
    const target = right.scrollHeight - right.clientHeight,
      d = target - right.scrollTop;
    if (Math.abs(d) < 1) {
      right.scrollTop = target;
      lastTop = right.scrollTop;
      following = false;
      return;
    }
    right.scrollTop += d > 0 ? Math.max(1, d * 0.4) : d;
    lastTop = right.scrollTop;
    requestAnimationFrame(followTick);
  }
  function feedChanged() {
    if (!followFeed) {
      newpill.classList.add('on');
      return;
    }
    if (!following) {
      following = true;
      requestAnimationFrame(followTick);
    }
  }
  function stepCard(step, url) {
    if (cards[step]) return cards[step];
    const el = document.createElement('div');
    el.className = 'card';
    el.dataset.step = step;
    el.innerHTML = `<details class="step"><summary><span class="chev">▸</span><span class="sn">Step ${step + 1}</span><span class="sa">observing…</span><span class="sv"></span></summary>
      <div class="u" title="${esc(url || '')}">${esc(url || '')}</div>
      <div class="tl"></div></details>`;
    feed.appendChild(el);
    cards[step] = el;
    $('s-step').textContent = step + 1;
    feedChanged();
    return el;
  }
  // The collapsed line: what was chosen and how it went. Detail stays inside, one click away.
  function summarize(step, action, verdict) {
    const s = stepCard(step).querySelector('summary');
    if (action !== undefined) s.querySelector('.sa').textContent = action;
    if (verdict !== undefined) s.querySelector('.sv').innerHTML = verdict;
  }
  // Per-step state, kept only for the collapsed line (what was done, how it went) and the outcome row.
  const STORY = {};
  function story(step, patch) {
    const st = (STORY[step] = Object.assign(STORY[step] || {}, patch));
    const card = stepCard(step);
    const sum = card.querySelector('summary .sa');
    const toText = (h) => {
      const t = document.createElement('div');
      t.innerHTML = h;
      return t.textContent;
    };
    const didTxt =
      st.did && st.did.length
        ? toText(st.did[st.did.length - 1])
        : st.answer
          ? `answer: ${st.answer}`
          : st.copied
            ? `found "${st.copied.text}" on the page`
            : st.chose && st.chose.none
              ? 'nothing more to do'
              : '';
    const ocTxt = st.result ? (st.result.accepted ? 'succeeded' : st.result.undone ? 'undone' : 'rejected') : '';
    if (didTxt) sum.textContent = didTxt.charAt(0).toUpperCase() + didTxt.slice(1) + (ocTxt ? ` — ${ocTxt}` : '');
  }
  // What an action reads like in a sentence: "clicked link 'Seattle'", "typed 'Boston' into 'Where from?'".
  function humanAction(e) {
    const d = String(e.desc || ''),
      head = d.split(' | ')[0];
    const m = /^(\w+) "(.*?)"/.exec(head);
    const role = m ? m[1] : '',
      name = m ? m[2] : head;
    if (e.action_kind === 'keyboard') {
      const t = /typed "(.*?)"/.exec(d);
      return `typed <b>"${esc(t ? t[1] : '')}"</b> into <b>${esc(role ? `${role} "${name}"` : head)}</b>`;
    }
    if (e.action_kind === 'scroll' || /^scroll/.test(e.id || ''))
      return d.includes('up') ? 'scrolled up' : 'scrolled down';
    if (e.action_kind === 'find' || /^find/.test(e.id || '')) {
      const v = /with value "(.*?)"/.exec(d);
      return `searched the page for <b>"${esc(v ? v[1] : '')}"</b>`;
    }
    if (e.action_kind === 'fill' || e.action_kind === 'fill_enter') {
      const v = /with value "(.*?)"/.exec(d);
      return `filled <b>${esc(role ? `${role} "${name}"` : head)}</b> with <b>"${esc(v ? v[1] : '')}"</b>`;
    }
    if (e.action_kind === 'select') {
      const v = /with value "(.*?)"/.exec(d);
      return `selected <b>"${esc(v ? v[1] : '')}"</b> in <b>${esc(name)}</b>`;
    }
    const verb =
      {
        link: 'opened',
        button: 'pressed',
        option: 'picked',
        checkbox: 'toggled',
        radio: 'picked',
        tab: 'switched to',
        menuitem: 'chose',
      }[role] || 'clicked';
    return `${verb} <b>${esc(role ? `${role} "${name}"` : head)}</b>`;
  }
  const SHORT_ACTION = {
    copy: 'to copy a value from the page',
    find: 'to find a text on the page',
    'scroll:down': 'to scroll down',
    'scroll:up': 'to scroll up',
  };
  const shortDesc = (id, desc) =>
    SHORT_ACTION[id] ||
    (/^scroll/.test(id) ? 'to scroll' : /^find/.test(id) ? 'to find a text on the page' : desc.split(' | ')[0]);
  const NOTE_WARN =
    /undo|undone|withdrawn|rejected|replan|re-plan|fail|wall|dead end|stop|abandon|taken away|discard|too large|giving up|give up|skipped|does not look like|refused/i;

  // ---- where the options of a question came from, drawn on the source text -------------------------
  // Mark every occurrence of each span in `text` (longest span first, no overlaps). `cls(i)` names the class
  // of the i-th mark in reading order.
  function markSpans(text, spans, cls) {
    const T = String(text || ''),
      low = T.toLowerCase(),
      taken = new Array(T.length).fill(false),
      hits = [];
    for (const sp of [...new Set(spans.filter((x) => x && x.length >= 2))].sort((a, b) => b.length - a.length)) {
      const sl = sp.toLowerCase();
      let from = 0,
        i;
      while ((i = low.indexOf(sl, from)) >= 0) {
        if (!taken.slice(i, i + sp.length).some(Boolean)) {
          hits.push([i, i + sp.length]);
          for (let k = i; k < i + sp.length; k++) taken[k] = true;
        }
        from = i + 1;
      }
    }
    hits.sort((a, b) => a[0] - b[0]);
    let out = '',
      pos = 0;
    hits.forEach(([a, b], n) => {
      out += esc(T.slice(pos, a)) + `<mark class="${cls ? cls(n, T.slice(a, b)) : ''}">${esc(T.slice(a, b))}</mark>`;
      pos = b;
    });
    return out + esc(T.slice(pos));
  }
  // The kind of a piece the final copy round offers -- the same patterns the harness cuts with (SPAN_RE),
  // applied to the piece: a piece always came from exactly one of them, or is a word, or the whole line.
  const PIECE_KINDS = [
    ['price', /^[$€£¥]\s?\d/],
    ['time', /^\d{1,2}:\d{2}(?:\s?[APap][Mm])?$/],
    [
      'date',
      /^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d|^\d{4}-\d{2}-\d{2}$|^\d{1,2}\/\d{1,2}(?:\/\d{2,4})?$/,
    ],
    ['code', /^[A-Z][A-Z0-9]{3,}$/],
    ['number with unit', /^\d[\d,]*(?:\.\d+)?\s?(?:%|km|mi|kg|lb|h|hr|hrs|min|nights?|guests?|adults?|rooms?|stars?)$/],
    ['capitalised phrase', /^[A-Z][a-zA-Z'&-]+(?: (?:[A-Z][a-zA-Z'&-]+|of|and|de|&)){1,5}$/],
  ];
  const pieceKind = (pc, line) =>
    pc.toLowerCase() === String(line || '').toLowerCase()
      ? 'the whole line'
      : (PIECE_KINDS.find(([, re]) => re.test(pc)) || ['a word'])[0];
  const cap = (html) => `<div class="srccap">${html}</div>`;
  // Returns HTML explaining the options of one question, or '' when the options are not cut from a text.
  function sourceOf(names, qs, st) {
    const crit = (n) => (qs[n] || {}).criteria || {};
    const opts = (n) => Object.keys(crit(n)).filter((k) => !['none', 'on_page'].includes(k));
    if (names.includes('value')) {
      const spans = opts('value');
      return (
        `<div class="src">${markSpans(st.task_goal, spans)}</div>` +
        cap(
          `The <b>marked pieces of the goal</b> are the options — cut by the code with fixed rules: text in quotes, ALL-CAPS codes (SEA, PVG), Capitalised phrases with the numbers attached to them (December 5, 2026), bare numbers. Plus <b>“a value shown on the page”</b> (read it off the page first) and <b>none</b>. Jev only picks; it never writes a value.`,
        )
      );
    }
    if (names.includes('wanted')) {
      const cl = opts('wanted');
      return (
        `<div class="src">${markSpans(st.task_goal, cl, (i) => (i % 2 ? 'alt' : ''))}</div>` +
        cap(
          `The goal cut into <b>clauses</b> at <span class="cut">. ; ! ? ,</span> (not the comma inside a date) and at <span class="cut">then</span> / <span class="cut">and</span>; each clause is one thing the goal asks for. The pick becomes what the copy is looking for and what the copied value is checked against.`,
        )
      );
    }
    if (names.includes('pick')) {
      const parts = Object.keys(crit('pick')).some((k) => /^part \d/.test(k));
      if (parts) {
        const n = opts('pick').length;
        return cap(
          `The page's text as the code reads it — one <b>unit</b> per table row, list item, paragraph or heading, plus each control with its value (“Where to?: Paris”) and the row it sits in — grouped in reading order into <b>${n} consecutive part${n === 1 ? '' : 's'}</b> of about equal length. Jev points at the part that contains the value; that part is cut again next round.`,
        );
      }
      const pieces = opts('pick'),
        line = st.line || pieces.find((pc) => pieces.every((o) => pc.toLowerCase().includes(o.toLowerCase()))) || '';
      const kinds = {};
      pieces.forEach((pc) => {
        const k = pieceKind(pc, line);
        (kinds[k] = kinds[k] || []).push(pc);
      });
      const strong = pieces.filter((pc) => !['a word', 'the whole line'].includes(pieceKind(pc, line)));
      const words = pieces.filter((pc) => pieceKind(pc, line) === 'a word');
      const marked = line
        ? `<div class="src">${markSpans(line, [...strong, ...words], (i, t) => (strong.some((x) => x.toLowerCase() === t.toLowerCase()) ? '' : 'word'))}</div>`
        : '';
      const legend = `<div class="kinds">${Object.entries(kinds)
        .map(([k, v]) => `<span class="k"><i>${esc(k)}:</i> ${v.map(esc).join(', ')}</span>`)
        .join('')}</div>`;
      return (
        marked +
        cap(
          `One line of the page, cut into <b>pieces</b>: first by pattern — prices, times, dates, CODES, numbers with a unit, Capitalised phrases (boxed) — then every word (dotted), then the whole line. Jev picks the piece that IS the value. Pieces already rejected are left out.`,
        ) +
        legend
      );
    }
    if (names.includes('target'))
      return cap(
        `One option per <b>visible, enabled control</b> on screen — buttons, links, fields, list options, checkboxes — with its label, current value and the row it sits in, plus <b>scroll</b>, <b>find a text</b> and <b>copy a value</b>, and <b>none</b>. Jev picks; the code never writes an option. Controls that failed three times are withdrawn.`,
      );
    if (names.includes('spell')) {
      const v = st.value_to_type || '',
        rem = st.remaining_to_type || '',
        typed = String(st.typed_so_far || '').replace(/[|\[\]]/g, '');
      const word = rem.split(' ')[0];
      return (
        `<div class="src">value to type <mark>${esc(v)}</mark> · field reads “${esc(typed)}” · still to type <mark class="alt">${esc(rem)}</mark>${word && word !== rem ? ` · next word <mark class="alt">${esc(word)}</mark>` : ''}</div>` +
        cap(
          `The code does the spelling — it knows what is left to type. Jev only decides: type the <b>next word</b>, type <b>the rest</b>, or <b>stop</b> because one of the site's suggestions already names what the goal wants.`,
        )
      );
    }
    if (names.includes('key'))
      return cap(
        `No piece of the goal fit this field, so Jev spells <b>key by key</b>: letters a–z, digits, space <span class="cut">- . , / @</span>, Backspace, arrows, and <b>done</b>.`,
      );
    if (names.includes('keep'))
      return cap(
        `The field already reads something that is not the value letter for letter. Jev judges whether it already <b>means</b> what the goal wants (another name for the same place, the same date in another format) — otherwise the code clears it and types the value.`,
      );
    if (names.includes('fact'))
      return cap(
        `The options are the <b>facts</b> given at the start plus every value <b>copied</b> so far (the register). Jev picks the one that belongs in this field, or none.`,
      );
    if (names.includes('option'))
      return cap(
        `The options are the control's <b>own choices</b> (a select's options, a picker's entries), read from the page.`,
      );
    if (names.includes('answer_form'))
      return cap(
        `Either <b>all the copied values together</b> (the register), <b>one value on this page</b> (a copy round follows), or <b>nothing</b>.`,
      );
    return '';
  }
  // One Jev call -> what a reader needs: the question in plain words, the pick, the closest alternatives,
  // and (on expansion) every option with its probability plus the raw exchange.
  const shortOpt = (k) => String(k).split(' | ')[0].slice(0, 60);
  const pc = (p) => `<span class="dp">${Math.round((p || 0) * 100)}%</span>`;
  function describeCall(e) {
    const qs = e.questions || {},
      as = e.answers || {},
      names = Object.keys(qs);
    const choice = (n) => (as[n] || {}).choice,
      probs = (n) => (as[n] || {}).probabilities || {},
      noul = (n) => (as[n] || {}).noul;
    const crit = (n) => (qs[n] || {}).criteria || {};
    const optText = (n, k, byText) => (byText && k !== 'none' && crit(n)[k] ? crit(n)[k] : k);
    const alts = (n, byText, keep = 2) =>
      Object.entries(probs(n))
        .filter(([k, v]) => k !== choice(n) && v >= 0.01)
        .sort((a, b) => b[1] - a[1])
        .slice(0, keep)
        .map(([k, v]) => `${esc(shortOpt(optText(n, k, byText)))} ${Math.round(v * 100)}%`)
        .join(' · ');
    const list = (n, byText) => {
      const pr = probs(n);
      const rows = Object.entries(pr).sort((a, b) => b[1] - a[1]);
      const ch = choice(n);
      return `<div class="cands">${rows.map(([k, v]) => `<div class="cand${k === ch ? ' chosen' : ''}"><span class="p">${fmt(v)}</span><span class="d" title="${esc(optText(n, k, byText))}"><span class="mini" style="width:${Math.round(v * 60)}px"></span>${esc(optText(n, k, byText))}</span></div>`).join('')}</div>`;
    };
    const yn = (v, t) => `${v >= (t ?? 0.5) ? 'yes' : 'no'} ${pc(v)}`;
    const raw = `<details><summary>the exact question and the state Jev was given</summary><pre>${esc(JSON.stringify(e.questions, null, 1))}</pre><pre>${esc(JSON.stringify(e.state, null, 1))}</pre></details>`;
    let q = '',
      a = '',
      alt = '',
      detail = '';
    if (names.includes('target')) {
      const ch = choice('target'),
        none = ch === 'none';
      q = 'Which action next?';
      a = none
        ? `<b>none</b> — nothing worth doing here ${pc(probs('target').none)}`
        : `<b>${esc(shortOpt(optText('target', ch, true)))}</b> ${pc(probs('target')[ch])}`;
      const heads = [];
      if (noul('done') !== undefined) heads.push(`task complete? ${Math.round(noul('done') * 100)}%`);
      if (noul('offpath') !== undefined) heads.push(`off path? ${Math.round(noul('offpath') * 100)}%`);
      alt = [alts('target', true) ? `also considered: ${alts('target', true)}` : '', heads.join(' · ')]
        .filter(Boolean)
        .join(' — ');
      detail = list('target', true);
    } else if (names.includes('value')) {
      const ch = choice('value');
      q = 'What goes in this field?';
      a =
        ch === 'on_page'
          ? `<b>a value shown on the page</b> — read it first ${pc(probs('value')[ch])}`
          : ch === 'none'
            ? `<b>nothing from the goal</b> ${pc(probs('value')[ch])}`
            : `<b>"${esc(ch)}"</b> ${pc(probs('value')[ch])}`;
      alt = alts('value') ? `also considered: ${alts('value')}` : '';
      detail = list('value');
    } else if (names.includes('fact')) {
      const ch = choice('fact');
      q = 'Which fact goes in this field?';
      a = `<b>${esc(ch)}</b> ${pc(probs('fact')[ch])}`;
      detail = list('fact');
    } else if (names.includes('option')) {
      const ch = choice('option');
      q = 'Which option of the control?';
      a = `<b>${esc(shortOpt(ch))}</b> ${pc(probs('option')[ch])}`;
      alt = alts('option') ? `also: ${alts('option')}` : '';
      detail = list('option');
    } else if (names.includes('keep')) {
      const ch = choice('keep');
      q = 'Keep the text already in the field?';
      a = `<b>${ch === 'keep' ? 'keep it' : 'replace it'}</b> ${pc(probs('keep')[ch])}`;
      detail = list('keep');
    } else if (names.includes('spell')) {
      const ch = choice('spell');
      q = 'Type the next word, or stop?';
      a = `<b>${{ word: 'type the next word', all: 'type the rest', done: 'stop — a suggestion already matches' }[ch] || esc(ch)}</b> ${pc(probs('spell')[ch])}`;
      detail = list('spell');
    } else if (names.includes('key')) {
      const ch = choice('key');
      q = 'Which key next?';
      a = `<b>${esc(ch)}</b> ${pc(probs('key')[ch])}`;
      detail = list('key');
    } else if (names.includes('commit')) {
      const ch = choice('commit');
      q = 'Press Enter now?';
      a = `<b>${ch === 'press_enter' ? 'yes' : 'no, leave the field'}</b> ${pc(probs('commit')[ch])}`;
      detail = list('commit');
    } else if (names.includes('wanted')) {
      const ch = choice('wanted');
      q = 'Which part of the goal is this value for?';
      a = ch === 'none' ? `<b>none</b>` : `<i>${esc(ch)}</i> ${pc(probs('wanted')[ch])}`;
      alt = alts('wanted') ? `also: ${alts('wanted')}` : '';
      detail = list('wanted');
    } else if (names.includes('pick')) {
      const parts = Object.keys(crit('pick')).some((k) => /^part \d/.test(k));
      const ch = choice('pick');
      q = parts ? 'Which part of the page contains the value?' : 'Which piece is the value?';
      a =
        ch === 'none'
          ? `<b>none of these</b>`
          : `<b>"${esc(shortOpt(optText('pick', ch, parts)))}"</b> ${pc(probs('pick')[ch])}`;
      alt = alts('pick', parts) ? `also: ${alts('pick', parts)}` : '';
      detail = list('pick', parts);
    } else if (names.includes('ok')) {
      q = 'Is that really the value?';
      a = `<b>${yn(noul('ok'), thresholds.verify)}</b>`;
    } else if (names.includes('answer_form')) {
      const ch = choice('answer_form');
      q = 'How to report the answer?';
      a = `<b>${{ all_copied: 'all the copied values together', one_on_page: 'one value on this page', none: 'nothing to report' }[ch] || esc(ch)}</b> ${pc(probs('answer_form')[ch])}`;
      detail = list('answer_form');
    } else if (names.includes('verify') || names.includes('result')) {
      const v = names.includes('verify') ? noul('verify') : probs('result').succeeded || 0;
      q = 'Did it work?';
      a = `<b>${yn(v, thresholds.verify)}</b>${noul('progress') !== undefined ? ` · closer to the goal? <b>${yn(noul('progress'), thresholds.progress)}</b>` : ''}`;
    } else if (names.includes('answers_question')) {
      const by = {
        risk: ['Would this commit something irreversible?', thresholds.risk],
        done: ['Is the task complete?', thresholds.done],
        offpath: ["Is this off the goal's path?", thresholds.offpath],
        not_applied: ['Provably never applied?', thresholds.not_applied],
        verify: ['Did it work?', thresholds.verify],
      };
      const [qq, t] = by[e.tag] || [e.tag, 0.5];
      q = qq;
      a = `<b>${yn(noul('answers_question'), t)}</b>`;
    } else {
      q = names.join(', ') || e.tag;
      a = esc(JSON.stringify(e.answers)).slice(0, 120);
    }
    return { q, a, alt, detail: sourceOf(names, qs, e.state || {}) + detail + raw };
  }
  // Rows of the step timeline: a decision (a Jev call) or a deed (something the code did).
  function decRow(step, e) {
    const qs = e.questions || {};
    const single =
      Object.keys(qs).length === 1 &&
      Object.values(qs).every((q) => q.type === 'choice' && Object.keys(q.criteria || {}).length <= 1);
    if (single) return; // only 'none' to choose from: no decision was made
    const S = (STORY[step] = STORY[step] || {});
    S.ncalls = (S.ncalls || 0) + 1;
    // typing: the field was focused before Jev was asked what goes in it -- say so, in its place in time
    if (!S.focused && (qs.value || qs.fact) && e.state && e.state.field) {
      S.focused = true;
      const had = String(e.state.typed_so_far || '').replace(/[|\[\]]/g, '');
      didRow(
        step,
        `focused <b>${esc(String(e.state.field).split(' | ')[0])}</b>${had ? ` <span class="sc">— it read "${esc(had)}"</span>` : ''}`,
      );
    }
    const d = describeCall(e),
      el = document.createElement('details');
    el.className = 'dec';
    el.innerHTML = `<summary><span class="who" title="a question Jev answered — click for every option it saw">Jev</span><span class="dq">${esc(d.q)}</span><span class="dl">${fmt(e.latency_s, 1)}s</span><span class="da">${d.a}</span>${d.alt ? `<span class="dalt">${d.alt}</span>` : ''}</summary><div class="dd">${d.detail}</div>`;
    stepCard(step).querySelector('.tl').appendChild(el);
    feedChanged();
  }
  // `who`: 'code' (the harness acted on the page, default), 'page' (what the browser showed).
  function didRow(step, html, warn, who = 'code') {
    const el = document.createElement('div');
    el.className = 'didrow ' + who + (warn ? ' warn' : '');
    el.innerHTML = `<span class="who" title="${who === 'page' ? 'what the browser showed' : 'what the harness did to the page'}">${who}</span><span class="dt">${html}</span>`;
    stepCard(step).querySelector('.tl').appendChild(el);
    feedChanged();
    return el;
  }
  const NOTE_SKIP =
    /^(value for this field|copying for|answer form|the value for this field is shown|no piece of the goal names)/;
  function finalCard(cls, title, html) {
    const el = document.createElement('div');
    el.className = 'card final ' + cls;
    el.innerHTML = `<h3>${title}</h3>${html}`;
    feed.appendChild(el);
    feedChanged();
  }

  const handlers = {
    start(e) {
      thresholds = e.thresholds || {};
      startedAt = Date.now();
      clearInterval(timer);
      timer = setInterval(tick, 1000);
      stopPlay();
      $('scrub').hidden = true;
      followFeed = true;
      newpill.classList.remove('on');
      Object.values(cards).forEach((c) => c.classList.remove('active'));
      setStatus('running', 'running');
      setPhase('', 'Opening ' + e.start);
      const el = document.createElement('div');
      el.className = 'card';
      const kb = /_kb$/.test(e.variant || '');
      el.innerHTML = `<h3>Run</h3><div style="font-size:12.5px">${esc(e.goal)}</div>
        <div class="hint">${e.has_code_check ? 'Ends when the code-owned check passes.' : 'No code-owned check: ends on Jev\'s own "done" signal — its opinion, not a verified result.'}${Object.keys(runMeta.facts || {}).length ? '' : kb ? ' No facts given: text fields are spelled from the goal, one word per judgment.' : ' No facts given and keyboard mode off: nothing can be typed.'}</div>
        <div class="hint">Decision lines: verify ≥ ${fmt(thresholds.verify)} accepts · off-path ≥ ${fmt(thresholds.offpath)} undoes · risk ≥ ${fmt(thresholds.risk)} marks an action irreversible · max ${e.max_steps} steps · keyboard mode ${/_kb$/.test(e.variant || '') ? 'ON (a field no fact fits is spelled from the goal, one key per judgment)' : 'off'} · observation ${e.viewport_only ? 'viewport only (scrolling is an action)' : 'whole page'}</div>`;
      feed.appendChild(el);
    },
    observe(e) {
      currentStep = e.step;
      lastPlan = null;
      stepCard(e.step, e.url);
      showShot(e.screenshot, e.url);
      setPhase('', `Step ${e.step + 1}: observing "${e.title || ''}" (${e.n_elements} actionable elements)`);
      story(e.step, { seen: { title: e.title, n: e.n_elements } });
      didRow(
        e.step,
        `<b>${esc(e.title || 'the page')}</b> <span class="sc">· ${e.n_elements} thing${e.n_elements === 1 ? '' : 's'} to act on</span>`,
        false,
        'page',
      );
      if (e.popups && e.popups.length) didRow(e.step, `the page showed a dialog: ${esc(e.popups.join(' | '))}`);
      if (e.acceptance && e.acceptance.length)
        didRow(e.step, `<span class="sc">code check: ${esc(e.acceptance.join(' · '))}</span>`);
    },
    judge(e) {
      setPhase('', `Step ${e.step + 1}: is it done? is it off the path?`);
      story(e.step, { judge: { done: e.done, offpath: e.offpath, doneAsked: !!e.done_asked } });
    },
    undo(e) {
      setPhase('undo', `Step ${e.step + 1}: undoing the last action`);
      summarize(e.step, 'off the path → undo the last action', '<span class="tag warn">↩ UNDO</span>');
      didRow(e.step, `went back — ${esc(e.reason)}`, true);
      showShot(e.screenshot);
    },
    plan(e) {
      lastPlan = e;
      setPhase('', `Step ${e.step + 1}: choosing among ${e.n_candidates} actions`);
      const first = e.ranked[0],
        second = e.ranked[1];
      story(e.step, {
        chose: first
          ? {
              desc: first.id === 'none' ? '' : shortDesc(first.id, first.desc),
              p: first.p,
              second: second ? second.p : null,
              none: first.id === 'none',
              isField: /^(combobox|textbox|searchbox)/.test(first.desc || ''),
            }
          : null,
      });
    },
    act(e) {
      setPhase('acting', `Step ${e.step + 1}: ${e.action_kind} → ${e.desc}`);
      showShot(e.screenshot);
      {
        const S = STORY[e.step] || {};
        const did = [
          ...(S.did || []),
          (e.attempt ? `<span class="tag warn">attempt ${e.attempt + 1}</span> ` : '') + humanAction(e),
        ];
        const patch = {
          did,
          chose: Object.assign({}, S.chose || {}, {
            verb: humanAction(e),
            none: false,
            isField: e.action_kind === 'keyboard',
          }),
          result: null,
        };
        if (e.attempt)
          patch.chose.p = (lastPlan && (lastPlan.ranked.find((r) => r.id === e.id) || {}).p) ?? patch.chose.p;
        story(e.step, patch);
      }
      const risk =
        e.side_effect === 'irreversible'
          ? ` <span class="tag bad">irreversible${e.risk !== null && e.risk !== undefined ? ' ' + Math.round(e.risk * 100) + '%' : ''}</span>`
          : '';
      if (e.action_kind !== 'keyboard' || e.attempt)
        didRow(
          e.step,
          `${e.attempt ? `<span class="tag warn">attempt ${e.attempt + 1}</span> ` : ''}${humanAction(e)}${risk}`,
        );
      else if (risk) didRow(e.step, `typing here is${risk}`);
    },
    verify(e) {
      setPhase(
        'verifying',
        `Step ${e.step + 1}: did it have the intended effect? ${fmt(e.verify)} → ${e.accepted ? 'accepted' : 'rejected'}`,
      );
      story(e.step, {
        result: {
          accepted: e.accepted,
          undone: e.undone,
          verify: e.verify,
          progress: e.accepted ? e.progress : null,
          retry: /retry|next-best/.test(e.outcome || ''),
          error: e.action_error || null,
        },
        shot: e.screenshot_after_undo || e.screenshot || (STORY[e.step] || {}).shot,
      });
      const tag = e.accepted
        ? `<span class="tag ok">✓ ${Math.round(e.verify * 100)}%</span>`
        : e.undone
          ? `<span class="tag warn">↩ ${Math.round(e.verify * 100)}%</span>`
          : `<span class="tag bad">✗ ${Math.round(e.verify * 100)}%</span>`;
      summarize(e.step, undefined, tag);
      const extras = [];
      if (e.settled_ms !== null && e.settled_ms !== undefined && e.settled_ms >= 900)
        extras.push(
          `waited ${(e.settled_ms / 1000).toFixed(1)}s for the page to finish loading${e.settled === false ? ' (still loading at the cap)' : ''}`,
        );
      if (e.reobserved) extras.push('looked again after a pause before judging');
      if (e.not_applied !== null && e.not_applied !== undefined)
        extras.push(`provably never applied? ${Math.round(e.not_applied * 100)}%`);
      if (e.action_error) didRow(e.step, `the page refused the action: ${esc(e.action_error)}`, true);
      let oc = e.accepted
        ? `<span class="ok">✓ it worked</span>${e.progress !== null && e.progress !== undefined ? (e.progress >= (thresholds.progress ?? 0.25) ? ', and the task moved forward' : ', <span class="warn">but the task did not move forward</span>') : ''}`
        : e.undone
          ? `<span class="warn">↩ not what was expected — undone</span>`
          : `<span class="bad">✗ not what was expected — rejected</span>`;
      if (!e.accepted && e.outcome) oc += ` <span class="sc">· ${esc(e.outcome)}</span>`;
      if (extras.length) oc += ` <span class="sc">· ${esc(extras.join(' · '))}</span>`;
      const shot = e.screenshot_after_undo || e.screenshot;
      if (shot)
        oc += `<img class="thumb" src="${img(shot)}" alt="the page after the action" title="click to show on the stage">`;
      const row = didRow(e.step, oc, false, 'page');
      row.classList.add('oc');
      const th = row.querySelector('.thumb');
      if (th)
        th.addEventListener('click', () => {
          stageImg().src = th.src;
        });
      showShot(shot);
    },
    note(e) {
      const st = e.step ?? currentStep ?? 0;
      if (stepCard(st).querySelector('summary .sa').textContent === 'observing…') summarize(st, e.text);
      if (NOTE_SKIP.test(e.text)) return; // said by a decision row already
      const warn = NOTE_WARN.test(e.text) || !!e.unmet;
      if (warn) {
        const S = STORY[st] || {};
        story(st, { notes: [...(S.notes || []), e.text] });
      }
      didRow(
        st,
        `${esc(e.text)}${e.unmet ? `<ul class="hist">${e.unmet.map((u) => `<li>${esc(u)}</li>`).join('')}</ul>` : ''}`,
        warn,
      );
    },
    copy(e) {
      COPIED[e.key] = e.text;
      summarize(e.step, `copied "${e.text}" → fact ${e.key}`, '<span class="tag info">COPIED</span>');
      {
        const S = STORY[e.step] || {};
        story(e.step, {
          copied: { text: e.text, wanted: e.wanted, ok: e.ok },
          did: [
            ...(S.did || []),
            `found <b>"${esc(e.text)}"</b> on the page${e.wanted && !/^fill the field/.test(e.wanted) ? ` (for: ${esc(e.wanted)})` : ''}`,
          ],
        });
      }
      didRow(
        e.step,
        `kept <b>"${esc(e.text)}"</b> as ${esc(e.key)} <span class="sc">— from the line "${esc(e.context)}"</span>`,
      );
    },
    approval(e) {
      const risk = e.risk !== undefined && e.risk !== null ? ` (risk ${Math.round(e.risk * 100)}%)` : '';
      if (e.status === 'pending') {
        $('approval-text').textContent = `Jev judges this irreversible${risk}: ${e.action}`;
        approval.hidden = false;
        setPhase('acting', `Step ${e.step + 1}: waiting for your decision on an irreversible action`);
        didRow(
          e.step,
          `waiting for the operator: <b>${esc(e.action)}</b> <span class="sc">is irreversible${esc(risk)}</span>`,
          true,
        );
        return;
      }
      approval.hidden = true;
      const who = e.decided_by === 'operator' ? 'you' : `the ${esc(e.policy)} policy`;
      didRow(
        e.step,
        e.status === 'allowed'
          ? `<b>allowed</b> by ${who} — performing <b>${esc(e.action)}</b>${esc(risk)}`
          : `<b>not performed</b> — ${who} said no to <b>${esc(e.action)}</b>${esc(risk)}`,
        e.status !== 'allowed',
      );
    },
    copy_trace(e) {
      /* every round of the selection is already a decision row (the Jev calls) */
      (e.attempts || []).forEach((a) => {
        if (a.text !== null && a.text !== undefined && !a.accepted)
          didRow(e.step, `discarded "${esc(a.text)}" — not the value wanted`, true);
      });
    },
    answer(e) {
      summarize(e.step, `answer: "${e.text}"`, '<span class="tag ok">ANSWER</span>');
      story(e.step, {
        answer: e.text,
        did: [...((STORY[e.step] || {}).did || []), `reported the answer <b>${esc(e.text)}</b>`],
      });
      didRow(e.step, `reported the answer <b>${esc(e.text)}</b> <span class="sc">— from "${esc(e.context)}"</span>`);
      const inAnswer = new Set(String(e.text).split(/;\s*/));
      const extra = Object.values(COPIED).filter((v) => !inAnswer.has(v));
      $('s-answer').textContent = extra.length ? `${e.text} · ${extra.join(' · ')}` : e.text;
      $('s-answer').title = e.context || '';
      $('s-answer-row').hidden = false;
    },
    key(e) {
      setPhase('acting', `Step ${e.step + 1}: typing into ${e.field} … "${e.typed}" → ${e.choice}`);
      const text = String(e.typed || '')
        .replace(/\|/g, '')
        .replace(/\[|\]/g, '');
      const tl = stepCard(e.step).querySelector('.tl'),
        last = tl.lastElementChild;
      if (e.choice === 'done') {
        if (text)
          story(e.step, {
            typed: { text, field: String(e.field).split(' | ')[0], left_open: (e.suggestions || []).length > 0 },
          });
        const sug = (e.suggestions || []).slice(0, 3);
        if (text)
          didRow(
            e.step,
            `the field now reads <b>"${esc(text)}"</b>${sug.length ? ` <span class="sc">— the site suggests: ${esc(sug.join(' · '))}</span>` : ''}`,
          );
      } else if (!['backspace', 'replace', 'left', 'right', 'space'].includes(e.choice)) {
        didRow(e.step, `typed <b>"${esc(e.choice)}"</b>`);
      } else if (e.choice === 'backspace' || e.choice === 'replace') {
        // one row for a run of deletions, not one per key
        if (last && last.dataset.kb === 'del') {
          last.dataset.n = String(Number(last.dataset.n) + 1);
          last.querySelector('.dt').innerHTML =
            `cleared <b>"${esc(last.dataset.was)}"</b> <span class="sc">— ${last.dataset.n} deletion${last.dataset.n === '1' ? '' : 's'}</span>`;
        } else {
          const r = didRow(e.step, `clearing <b>"${esc(text)}"</b>`);
          r.dataset.kb = 'del';
          r.dataset.n = e.choice === 'replace' ? '0' : '1';
          r.dataset.was = text;
        }
      }
    },
    jev(e) {
      $('s-calls').textContent = e.calls_total;
      $('s-tok').textContent = e.tokens_total.toLocaleString();
      $('s-cost').textContent = '$' + Number(e.cost_est_usd).toFixed(4);
      decRow(currentStep ?? 0, e);
    },
    end(e) {
      endSeen = true;
      clearInterval(timer);
      tick();
      const why = {
        user_stop: 'Stopped by you.',
        stopped_on_done_signal:
          'Stopped on Jev\'s own "done" signal. There was no code-owned check, so this is its opinion, not a verified result.',
        gave_up_none_streak:
          'Gave up: "no action" led three plans in a row while Jev\'s done score stayed below the bar. Something the goal asks for was probably not reached — check the copied values against the goal.',
        gave_up_checklist_unmet: 'Gave up: Jev kept saying done but the code-owned check never passed.',
        budget_exhausted: 'Stopped: step/time/backtrack budget exhausted.',
        max_steps: 'Stopped: reached the step limit without Jev ever signalling done.',
        stalled_in_widget:
          'Stopped: the last few accepted actions all hit the same repeated widget (a calendar, a list) without the done signal rising — the loop was going in circles.',
        cycle: 'Stopped: the same state kept recurring.',
        dead_end: 'Stopped: every alternative here failed verification and the least-bad one had no effect.',
        no_candidates: 'Stopped: nothing left to try on this page.',
        site_unavailable: 'The start page exposed no actionable element.',
        escalate_unconfirmed_irreversible:
          'Escalated: an irreversible action could not be confirmed and must not be retried.',
        escalate_offpath_after_irreversible:
          'Escalated: went off path right after an irreversible action, which cannot be undone.',
        escalate_no_safe_action: 'Escalated: the only remaining option is irreversible and its evidence failed.',
      };
      const cls = e.success ? '' : e.stopped === 'stopped_on_done_signal' && !e.escalated ? '' : 'bad'; // no completion check: a done-signal stop is neither pass nor fail
      const title = e.success
        ? 'Done — the code-owned check passed'
        : e.stopped === 'stopped_on_done_signal'
          ? "Stopped on Jev's done signal"
          : 'Stopped: ' + (e.stopped || 'unknown');
      finalCard(
        cls,
        title,
        `<div style="font-size:12.5px">${esc(why[e.stopped] || (e.success ? 'The completion check the code owns is satisfied.' : ''))}</div>
        ${e.unmet ? `<div class="note">unmet at stop: ${esc(e.unmet.join(' · '))}</div>` : ''}
        <div class="hint">${e.steps} steps · ${e.jev_calls} Jev calls · ${e.in_tok.toLocaleString()} tokens · ${e.backtracks} undo${e.backtracks === 1 ? '' : 's'} · ${e.seconds}s</div>
        <div class="hint" style="margin-top:6px">Actions that stood:</div><ol class="hist">${(e.history || []).map((h) => `<li>${esc(h)}</li>`).join('') || '<li>(none)</li>'}</ol>`,
      );
      setStatus(e.success ? 'done' : 'stopped', e.success ? 'done' : 'stopped');
      setPhase(e.success ? 'done' : 'stopped', title);
    },
    error(e) {
      finalCard(
        'err',
        'Error',
        `<pre class="q" style="white-space:pre-wrap">${esc(e.error)}\n\n${esc(e.traceback || '')}</pre>`,
      );
      setStatus('stopped', 'error');
      setPhase('stopped', 'The run crashed; see the error card.');
    },
    closed() {
      clearInterval(timer);
      tick();
      stopFrames();
      $('run').disabled = false;
      $('stop').disabled = true;
      $('clear').disabled = false;
      if (es) {
        es.close();
        es = null;
      }
      if (!endSeen && $('status').textContent === 'running') setStatus('stopped', 'stopped');
      buildScrubber();
    },
  };

  // Rendering is paced. A step's events arrive in a burst (three Jev answers in 300 ms), and painted
  // as they arrive the column flickers past before it can be read. Live events are rendered one at a
  // time at a reading rhythm, faster when the backlog grows; a replay (events older than a few
  // seconds, e.g. after a reload) renders at once. The stage is unaffected: frames bypass this queue.
  const renderQ = [];
  let draining = false;
  function enqueue(k, d) {
    renderQ.push([k, d]);
    if (!draining) drain();
  }
  function drain() {
    if (!renderQ.length) {
      draining = false;
      return;
    }
    draining = true;
    const [k, d] = renderQ.shift();
    try {
      handlers[k](d);
    } catch (err) {
      console.error(k, err);
    }
    const isReplay = (d.t && Date.now() / 1000 - d.t > 5) || renderQ.length > 15; // old events, or a burst no live run produces
    const delay = isReplay ? 0 : Math.max(20, Math.min(90, 300 / (renderQ.length + 1)));
    setTimeout(drain, delay);
  }

  // Keyframes for the replay scrubber: every screenshot the run produced, in order, with what it shows.
  const KEYFRAMES = [];
  const COPIED = {}; // copied_N -> text, for the answer line at the end
  function keyframe(kind, d) {
    const push = (b64, label) => {
      if (b64 && !KEYFRAMES.some((f) => f.seq === d.seq && f.label === label))
        KEYFRAMES.push({ b64, step: d.step, label, url: d.url || null, seq: d.seq, t: d.t || null, ct: Date.now() });
    };
    if (kind === 'observe') push(d.screenshot, `Step ${d.step + 1} · observe`);
    else if (kind === 'act') push(d.screenshot, `Step ${d.step + 1} · ${d.action_kind} → ${d.desc}`);
    else if (kind === 'verify') {
      push(d.screenshot, `Step ${d.step + 1} · after the action: ${d.outcome || ''}`);
      push(d.screenshot_after_undo, `Step ${d.step + 1} · after undo`);
    } else if (kind === 'undo') push(d.screenshot, `Step ${d.step + 1} · undo`);
  }
  // The replay timeline. When the screencast was recorded (a run watched live in this tab) it is
  // every frame, each labelled with the latest decision point reached by then, so scrubbing shows the
  // page moving between decisions. After a reload only the decision-point screenshots exist (the
  // server replays events, not frames), and the timeline falls back to those.
  let TL = [];
  function buildTimeline() {
    if (FRAMES.length > KEYFRAMES.length) {
      let k = 0;
      const out = [];
      for (const f of FRAMES) {
        while (k + 1 < KEYFRAMES.length && KEYFRAMES[k + 1].ct <= f.t) k++;
        const kf = KEYFRAMES[k] && KEYFRAMES[k].ct <= f.t ? KEYFRAMES[k] : null;
        out.push({
          b64: f.b64,
          ms: f.t,
          step: kf ? kf.step : null,
          label: kf ? kf.label : 'starting',
          url: kf ? kf.url : null,
        });
      }
      return out;
    }
    const t0 = KEYFRAMES.length ? KEYFRAMES[0].t : null;
    return KEYFRAMES.map((f) => ({
      b64: f.b64,
      ms: f.t && t0 ? (f.t - t0) * 1000 : null,
      step: f.step,
      label: f.label,
      url: f.url,
    }));
  }
  function showKeyframe(i) {
    const f = TL[i];
    if (!f) return;
    paintB64(f.b64);
    const el = f.ms !== null && TL[0].ms !== null ? ` · +${((f.ms - TL[0].ms) / 1000).toFixed(1)}s` : '';
    $('sc-label').textContent = `${i + 1}/${TL.length}${el} · ${f.label}`;
    $('sc-label').title = f.label;
    if (f.url) $('url').textContent = f.url;
    Object.values(cards).forEach((c) => c.classList.remove('active'));
    const c = f.step !== null ? cards[f.step] : null;
    if (c && !c.classList.contains('active')) {
      c.classList.add('active');
      followFeed = false;
      newpill.classList.remove('on');
      c.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
  }
  function buildScrubber() {
    TL = buildTimeline();
    if (!TL.length) return;
    const r = $('sc-range');
    r.max = TL.length - 1;
    r.value = TL.length - 1;
    $('scrub').hidden = false;
    showKeyframe(TL.length - 1);
  }
  // Playback at the run's real pace: each frame stays on screen for as long as the run took to
  // reach the next one. At the end, play again from the start.
  let playTimer = null;
  const playing = () => playTimer !== null;
  function stopPlay() {
    if (playTimer) clearTimeout(playTimer);
    playTimer = null;
    $('sc-play').textContent = '▶';
    $('sc-play').classList.remove('on');
    $('sc-play').setAttribute('aria-label', 'play');
  }
  function playFrom(i) {
    const r = $('sc-range');
    r.value = i;
    showKeyframe(i);
    if (i >= TL.length - 1) {
      stopPlay();
      return;
    }
    const a = TL[i].ms,
      b = TL[i + 1].ms;
    const dt = a !== null && b !== null ? Math.min(3000, Math.max(16, b - a)) : 700;
    playTimer = setTimeout(() => playFrom(i + 1), dt);
  }
  function togglePlay() {
    if (playing()) {
      stopPlay();
      return;
    }
    const r = $('sc-range');
    let i = Number(r.value);
    if (i >= TL.length - 1) i = 0;
    $('sc-play').textContent = '❚❚';
    $('sc-play').classList.add('on');
    $('sc-play').setAttribute('aria-label', 'pause');
    playFrom(i);
  }

  // ---- GIF export: the whole timeline, encoded in this page (no library, no upload). Each frame is
  // scaled to GIF_W, quantized to its own 255-colour palette (median cut), and written as a delta
  // against the previous frame: pixels that did not change are transparent, which is what keeps a
  // mostly-static page small. Frames closer together than GIF_MIN_MS are merged into one longer hold.
  const GIF_W = 800,
    GIF_MIN_MS = 80,
    GIF_MAX_FRAMES = 600;
  function lzwEncode(indices, minCodeSize) {
    // GIF LZW as the GIF89a specification describes it (variable code size, clear at 4096): the code size grows
    // when the next code to be assigned no longer fits, before it is assigned.
    const out = [];
    let cur = 0,
      curBits = 0;
    const emit = (code, size) => {
      cur |= code << curBits;
      curBits += size;
      while (curBits >= 8) {
        out.push(cur & 255);
        cur >>= 8;
        curBits -= 8;
      }
    };
    const clear = 1 << minCodeSize,
      eoi = clear + 1;
    let dict = new Map(),
      next = eoi + 1,
      size = minCodeSize + 1;
    emit(clear, size);
    let prefix = indices.length ? indices[0] : 0;
    for (let i = 1; i < indices.length; i++) {
      const k = indices[i],
        key = prefix * 4096 + k,
        got = dict.get(key);
      if (got !== undefined) {
        prefix = got;
        continue;
      }
      emit(prefix, size);
      if (next === 4096) {
        emit(clear, size);
        dict = new Map();
        next = eoi + 1;
        size = minCodeSize + 1;
      } else {
        if (next >= 1 << size) size++;
        dict.set(key, next++);
      }
      prefix = k;
    }
    emit(prefix, size);
    if (next >= 1 << size && size < 12) size++; // the decoder widens on the code we just assigned too
    emit(eoi, size);
    if (curBits > 0) out.push(cur & 255);
    return Uint8Array.from(out);
  }
  function medianCut(rgb, count) {
    // rgb: flat [r,g,b, r,g,b, ...] sample. Returns up to `count` [r,g,b] centroids.
    const n = rgb.length / 3,
      order = new Uint32Array(n);
    for (let i = 0; i < n; i++) order[i] = i;
    const box = (lo, hi) => {
      const mn = [255, 255, 255],
        mx = [0, 0, 0];
      for (let i = lo; i < hi; i++) {
        const o = order[i] * 3;
        for (let c = 0; c < 3; c++) {
          const v = rgb[o + c];
          if (v < mn[c]) mn[c] = v;
          if (v > mx[c]) mx[c] = v;
        }
      }
      const span = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]];
      return { lo, hi, span, w: Math.max(...span) * Math.log(hi - lo + 1) };
    };
    const boxes = n ? [box(0, n)] : [];
    while (boxes.length < count) {
      let bi = -1,
        best = 0;
      boxes.forEach((b, i) => {
        if (b.hi - b.lo > 1 && b.w > best) {
          best = b.w;
          bi = i;
        }
      });
      if (bi < 0) break;
      const b = boxes[bi],
        ch = b.span.indexOf(Math.max(...b.span));
      const part = Array.from(order.subarray(b.lo, b.hi)).sort((x, y) => rgb[x * 3 + ch] - rgb[y * 3 + ch]);
      order.set(part, b.lo);
      const mid = b.lo + ((b.hi - b.lo) >> 1);
      boxes.splice(bi, 1, box(b.lo, mid), box(mid, b.hi));
    }
    return boxes.map((b) => {
      const s = [0, 0, 0];
      for (let i = b.lo; i < b.hi; i++) {
        const o = order[i] * 3;
        s[0] += rgb[o];
        s[1] += rgb[o + 1];
        s[2] += rgb[o + 2];
      }
      const m = b.hi - b.lo;
      return [Math.round(s[0] / m), Math.round(s[1] / m), Math.round(s[2] / m)];
    });
  }
  function nearestIndexer(pal) {
    const cache = new Map();
    return (r, g, b) => {
      const key = ((r >> 2) << 12) | ((g >> 2) << 6) | (b >> 2);
      let idx = cache.get(key);
      if (idx !== undefined) return idx;
      let best = 1e9;
      idx = 0;
      for (let i = 0; i < pal.length; i++) {
        const dr = pal[i][0] - r,
          dg = pal[i][1] - g,
          db = pal[i][2] - b,
          d = dr * dr + dg * dg + db * db;
        if (d < best) {
          best = d;
          idx = i;
          if (d === 0) break;
        }
      }
      cache.set(key, idx);
      return idx;
    };
  }
  async function frameImageData(b64, w, h) {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' }));
    const cv = document.createElement('canvas');
    cv.width = w;
    cv.height = h;
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, w, h);
    const s = Math.min(w / bmp.width, h / bmp.height),
      dw = Math.round(bmp.width * s),
      dh = Math.round(bmp.height * s);
    ctx.drawImage(bmp, (w - dw) / 2, (h - dh) / 2, dw, dh);
    bmp.close();
    return ctx.getImageData(0, 0, w, h).data;
  }
  const SAME = (a, b, p) => Math.abs(a[p] - b[p]) + Math.abs(a[p + 1] - b[p + 1]) + Math.abs(a[p + 2] - b[p + 2]) < 12;
  async function exportGif() {
    if (!TL.length) return;
    const btn = $('sc-gif'),
      label = $('sc-label'),
      keep = label.textContent;
    btn.disabled = true;
    stopPlay();
    try {
      // Which frames go in: drop frames that follow the previous kept one too closely, then thin to the cap.
      let picks = [];
      let lastMs = -Infinity;
      TL.forEach((f, i) => {
        const ms = f.ms === null ? i * 700 : f.ms;
        if (ms - lastMs >= GIF_MIN_MS || i === TL.length - 1) {
          picks.push({ i, ms });
          lastMs = ms;
        }
      });
      if (picks.length > GIF_MAX_FRAMES) {
        const step = picks.length / GIF_MAX_FRAMES;
        picks = Array.from({ length: GIF_MAX_FRAMES }, (_, k) => picks[Math.floor(k * step)]);
      }
      const W = GIF_W,
        H = Math.round((GIF_W * STAGE_H) / STAGE_W);
      const chunks = [];
      const put = (arr) => {
        const u = arr instanceof Uint8Array ? arr : Uint8Array.from(arr);
        chunks.push(u);
        return u;
      };
      const lo = (v) => v & 255,
        hi = (v) => (v >> 8) & 255;
      put([0x47, 0x49, 0x46, 0x38, 0x39, 0x61, lo(W), hi(W), lo(H), hi(H), 0x00, 0x00, 0x00]); // header, no global palette
      put([0x21, 0xff, 0x0b, ...[...'NETSCAPE2.0'].map((c) => c.charCodeAt(0)), 0x03, 0x01, 0x00, 0x00, 0x00]); // loop forever
      let prev = null,
        lastGce = null;
      const TRANSPARENT = 255;
      for (let n = 0; n < picks.length; n++) {
        const { i, ms } = picks[n];
        const nextMs = n + 1 < picks.length ? picks[n + 1].ms : ms + 1500;
        const delay = Math.max(2, Math.min(300, Math.round((nextMs - ms) / 10))); // centiseconds; the last frame holds 1.5s
        label.textContent = `GIF: frame ${n + 1}/${picks.length}…`;
        const px = await frameImageData(TL[i].b64, W, H);
        // palette from a sample of the pixels that CHANGED (first frame: all of them)
        const sample = [];
        for (let p = 0; p < px.length; p += 4 * 9) {
          if (prev && SAME(px, prev, p)) continue;
          sample.push(px[p], px[p + 1], px[p + 2]);
        }
        if (prev && sample.length === 0) {
          // nothing changed: the previous frame simply stays up longer
          const d = (lastGce[4] | (lastGce[5] << 8)) + delay;
          lastGce[4] = d & 255;
          lastGce[5] = (d >> 8) & 255;
          continue;
        }
        const pal = medianCut(sample, 255);
        while (pal.length < 256) pal.push([0, 0, 0]);
        const near = nearestIndexer(pal.slice(0, 255));
        const idx = new Uint8Array(W * H);
        for (let p = 0, q = 0; p < px.length; p += 4, q++)
          idx[q] = prev && SAME(px, prev, p) ? TRANSPARENT : near(px[p], px[p + 1], px[p + 2]);
        lastGce = put([0x21, 0xf9, 0x04, 0x05, lo(delay), hi(delay), TRANSPARENT, 0x00]); // graphic control: disposal 1 (keep), transparency on 255
        put([0x2c, 0, 0, 0, 0, lo(W), hi(W), lo(H), hi(H), 0x87]); // image descriptor, local palette of 256
        put(pal.flat());
        put([8]);
        const lzw = lzwEncode(idx, 8);
        const sub = new Uint8Array(lzw.length + Math.ceil(lzw.length / 255) + 1);
        let o = 0,
          q = 0;
        while (o < lzw.length) {
          const len = Math.min(255, lzw.length - o);
          sub[q++] = len;
          sub.set(lzw.subarray(o, o + len), q);
          q += len;
          o += len;
        }
        sub[q++] = 0;
        put(sub.subarray(0, q));
        prev = px;
        if (n % 2 === 1) await new Promise((res) => setTimeout(res, 0)); // keep the page responsive
      }
      put([0x3b]);
      const blob = new Blob(chunks, { type: 'image/gif' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `jevonly-${(runMeta.started || new Date().toISOString()).replace(/[:.]/g, '-').slice(0, 19)}.gif`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      label.textContent = `GIF exported: ${picks.length} frames, ${(blob.size / 1048576).toFixed(1)} MB`;
      setTimeout(() => {
        if (label.textContent.startsWith('GIF exported')) label.textContent = keep;
      }, 4000);
    } catch (err) {
      console.error(err);
      label.textContent = 'GIF export failed: ' + ((err && err.message) || err);
    } finally {
      btn.disabled = false;
    }
  }
  $('sc-gif').addEventListener('click', exportGif);
  $('sc-play').addEventListener('click', togglePlay);
  $('sc-range').addEventListener('input', (e) => {
    stopPlay();
    showKeyframe(Number(e.target.value));
  });
  const nudge = (d) => {
    stopPlay();
    const r = $('sc-range');
    r.value = Math.max(0, Math.min(Number(r.max), Number(r.value) + d));
    showKeyframe(Number(r.value));
  };
  $('sc-prev').addEventListener('click', () => nudge(-1));
  $('sc-next').addEventListener('click', () => nudge(1));
  document.addEventListener('keydown', (e) => {
    if (
      $('scrub').hidden ||
      (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && document.activeElement.id !== 'sc-range')
    )
      return;
    if (e.key === 'ArrowLeft') {
      nudge(-1);
      e.preventDefault();
    }
    if (e.key === 'ArrowRight') {
      nudge(1);
      e.preventDefault();
    }
    if (e.key === ' ') {
      togglePlay();
      e.preventDefault();
    }
  });

  function connect(since) {
    if (es) es.close();
    es = new EventSource('/events?since=' + (since || 0));
    Object.keys(handlers).forEach((k) =>
      es.addEventListener(k, (ev) => {
        try {
          const d = JSON.parse(ev.data);
          if (k === 'start') {
            KEYFRAMES.length = 0;
            FRAMES.length = 0;
            frameBytes = 0;
            lastFrameB64 = null;
          }
          record(k, d);
          keyframe(k, d);
          enqueue(k, d);
        } catch (err) {
          console.error(k, err);
        }
      }),
    );
    es.onerror = () => {
      /* EventSource reconnects on its own with Last-Event-ID */
    };
  }

  // ---------------------------------------------------------------- log export
  function record(kind, d) {
    const e = {};
    for (const [k, v] of Object.entries(d)) e[k] = k.startsWith('screenshot') && v ? '<jpeg omitted>' : v;
    if (e.seq === undefined || !LOG.some((x) => x.seq === e.seq)) LOG.push(e); // a replay must not duplicate
  }
  const j1 = (o) => JSON.stringify(o);
  const has = (v) => v !== null && v !== undefined;
  function transcript() {
    const t0 = LOG.length ? LOG[0].t : 0;
    const ts = (e) => ('+' + ((e.t || t0) - t0).toFixed(1) + 's').padStart(8);
    const L = ['# JevOnly run log'];
    if (runMeta.goal) {
      L.push(
        `goal: ${runMeta.goal}`,
        `start: ${runMeta.start}`,
        `facts: ${j1(runMeta.facts || {})}`,
        `completion check: ${runMeta.has_code_check ? j1(runMeta.check) + " (code-owned; Jev's done is advisory)" : "none (loop stops on Jev's own done signal)"}`,
        `variant: ${runMeta.variant} · max steps: ${runMeta.max_steps} · time budget: ${runMeta.budget_s}s · started: ${runMeta.started}`,
      );
    }
    L.push('(compact: decisions only, no page state — Download .jsonl has everything)', '');
    // Per-step Jev totals: the calls themselves are not listed (their answers are already the judge /
    // next / verify lines below), only how many and how many tokens.
    let stepCalls = 0,
      stepTok = 0,
      lastField = null;
    const flushJev = () => {
      if (stepCalls) {
        L.push(
          `${' '.repeat(8)}   jev: ${stepCalls} call${stepCalls === 1 ? '' : 's'}, ${stepTok.toLocaleString()} tok`,
        );
        stepCalls = 0;
        stepTok = 0;
      }
    };
    const short = (s, n) => {
      s = String(s ?? '');
      return s.length > n ? s.slice(0, n - 1) + '…' : s;
    };
    for (const e of LOG) {
      const p = ts(e);
      switch (e.kind) {
        case 'start':
          L.push(
            `${p} START variant=${e.variant} code_check=${e.has_code_check} max_steps=${e.max_steps} viewport_only=${!!e.viewport_only} thresholds ${j1(e.thresholds)}`,
          );
          if (e.browser)
            L.push(
              `${p}   browser webdriver=${e.browser.webdriver} headless=${e.browser.headless} profile=${e.browser.profile} channel=${e.browser.channel} ua=${short(e.browser.ua || '', 90)}`,
            );
          break;
        case 'observe':
          flushJev();
          lastField = null;
          L.push(
            '',
            `${p} ── STEP ${e.step + 1} ── ${short(e.url, 100)}`,
            `${p}   observe elements=${e.n_elements}${e.popups ? ' popups=' + j1(e.popups) : ''}${e.acceptance ? ' code_check=' + j1(e.acceptance) : ''}`,
          );
          break;
        case 'judge':
          L.push(`${p}   done=${e.done_asked ? fmt(e.done) : 'n/a'} offpath=${fmt(e.offpath)}`);
          break;
        case 'plan':
          L.push(
            `${p}   next  ${e.ranked
              .slice(0, 4)
              .map((r) => `${fmt(r.p)} ${short(r.desc, 70)}`)
              .join(' | ')}${e.n_dead ? ` (dead=${e.n_dead})` : ''}`,
          );
          break;
        case 'act':
          L.push(
            `${p}   ACT   ${e.action_kind} ${short(e.desc, 110)}${has(e.value) ? ' value=' + j1(e.value) : ''}${has(e.risk) ? ' risk=' + fmt(e.risk) : ''}${e.attempt ? ` (attempt ${e.attempt + 1})` : ''}`,
          );
          break;
        case 'verify':
          L.push(
            `${p}   VERIFY ${fmt(e.verify)}${has(e.progress) ? ' progress=' + fmt(e.progress) : ''} -> ${e.outcome}${e.changed ? '' : ' (state unchanged)'}${e.reobserved ? ' (re-observed)' : ''}${e.action_error ? ' error=' + j1(short(e.action_error, 80)) : ''}`,
          );
          break;
        case 'undo':
          L.push(`${p}   UNDO  ${e.reason}`);
          break;
        case 'key':
          if (e.field !== lastField) {
            lastField = e.field;
            L.push(`${p}   keyboard -> ${short(e.field, 80)}`);
          }
          L.push(
            `${p}     ${e.choice === 'space' ? '␣' : e.choice === 'backspace' ? '⌫' : e.choice === 'left' ? '←' : e.choice === 'right' ? '→' : e.choice} p=${fmt(e.p)} field="${e.typed ?? ''}"${(e.suggestions || []).length ? ' sugg=' + j1(e.suggestions.slice(0, 3).map((s) => short(s, 50))) : ''}`,
          );
          break;
        case 'note':
          L.push(`${p}   NOTE  ${e.text}${e.unmet ? ' unmet=' + j1(e.unmet) : ''}`);
          break;
        case 'copy':
          L.push(
            `${p}   COPY  "${e.text}" -> fact ${e.key} (${e.rounds} rounds, p=${fmt(e.p)}${e.ok != null ? `, ok=${fmt(e.ok)}` : ''})${e.wanted ? ` for \`${e.wanted}\`` : ''} from: ${short(e.context, 100)}`,
          );
          break;
        case 'copy_trace': {
          if (e.wanted_probs)
            L.push(
              `${p}   SELECT for which clause? ${Object.entries(e.wanted_probs)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 4)
                .map(([k, v]) => `${k === (e.wanted || 'none') ? '*' : ''}${short(k, 50)} ${fmt(v)}`)
                .join(' | ')}`,
            );
          (e.attempts || []).forEach((a, ai) => {
            (a.rounds || []).forEach((r, i) =>
              L.push(
                `${p}   SELECT ${e.attempts.length > 1 ? `try ${ai + 1} ` : ''}round ${i + 1} (${r.kind}): ${r.options
                  .slice()
                  .sort((x, y) => y.p - x.p)
                  .slice(0, 4)
                  .map((o) => `${o.id === r.pick ? '*' : ''}${short(o.text, 60)} ${fmt(o.p)}`)
                  .join(' | ')}${r.pick === 'none' ? ' | *none' : ''}`,
              ),
            );
            L.push(
              `${p}   SELECT ${a.text != null ? `${a.accepted ? 'kept' : 'discarded'} "${a.text}"${a.ok != null ? ` ok=${fmt(a.ok)}` : ''}` : 'nothing chosen'}`,
            );
          });
          break;
        }
        case 'answer':
          L.push(`${p}   ANSWER "${e.text}" (${e.rounds} rounds, p=${fmt(e.p)}) from: ${short(e.context, 100)}`);
          break;
        case 'jev':
          stepCalls++;
          stepTok += e.in_tok || 0;
          break;
        case 'end':
          flushJev();
          L.push(
            '',
            `${p} END success=${e.success} stopped=${e.stopped || 'none'} steps=${e.steps} jev_calls=${e.jev_calls} tokens=${(e.in_tok || 0).toLocaleString()} undos=${e.backtracks} seconds=${e.seconds}${e.unmet ? ' unmet=' + j1(e.unmet) : ''}${e.answer ? ` answer="${e.answer.text}"` : ''}${e.copied && Object.keys(e.copied).length ? ` copied=${j1(e.copied)}` : ''}`,
            ...(e.history || []).map((h, i) => `${p}   ${i + 1}. ${h}`),
          );
          break;
        case 'error':
          L.push(
            `${p} ERROR ${e.error}`,
            ...(e.traceback || '')
              .split('\n')
              .slice(-6)
              .map((l) => `${p}   ${l}`),
          );
          break;
        case 'closed':
          flushJev();
          L.push(`${p} closed calls=${e.calls_total} tokens=${e.tokens_total} cost_est=$${e.cost_est_usd}`);
          break;
        default:
          break;
      }
    }
    return L.join('\n') + '\n';
  }
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove('show'), 2600);
  }
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      /* non-secure context or permission: fall through */
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch (e) {
      ok = false;
    }
    ta.remove();
    return ok;
  }
  $('copy').addEventListener('click', async () => {
    const text = transcript();
    const ok = await copyText(text);
    toast(
      ok
        ? `Copied ${LOG.length} events, ${(text.length / 1024).toFixed(0)} KB`
        : 'Clipboard refused; use Download .jsonl instead',
    );
  });
  $('dl').addEventListener('click', () => {
    const body = JSON.stringify({ meta: runMeta }) + '\n' + LOG.map((e) => JSON.stringify(e)).join('\n') + '\n';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([body], { type: 'application/x-ndjson' }));
    a.download = `jevonly-${(runMeta.started || new Date().toISOString()).replace(/[:.]/g, '-')}.jsonl`;
    a.click();
    URL.revokeObjectURL(a.href);
  });

  $('run').addEventListener('click', async () => {
    $('err').textContent = '';
    const kind = $('check-kind').value;
    const pairs = factPairs();
    if (pairs.some(([k, v]) => !k && v)) {
      $('err').textContent = 'Every fact needs a name on the left.';
      return;
    }
    const payload = {
      goal: $('goal').value,
      start: $('start').value,
      facts: Object.fromEntries(pairs.filter(([k]) => k)),
      key: $('key').value,
      max_steps: Number($('steps').value),
      budget_s: Number($('budget').value),
      keyboard: $('kb').checked,
      viewport_only: $('vp').checked,
      headed: $('headed').checked,
      max_cands: Number($('cap').value),
      check: kind === 'none' ? {} : { [kind]: $('check-val').value },
      thresholds: Object.fromEntries(['verify', 'offpath', 'done', 'risk'].map((k) => [k, Number($('t-' + k).value)])),
      irreversible: $('irreversible').value,
    };
    const r = await fetch('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const j = await r.json();
    if (!r.ok) {
      $('err').textContent = j.error || 'refused';
      return;
    }
    feed.innerHTML = '';
    cards = {};
    currentStep = null;
    endSeen = false;
    startedAt = null;
    clearInterval(timer);
    Object.keys(STORY).forEach((k) => delete STORY[k]);
    LOG = [];
    runMeta = {
      goal: payload.goal,
      start: payload.start,
      facts: j.facts,
      variant: j.variant,
      has_code_check: j.has_code_check,
      check: payload.check,
      max_steps: payload.max_steps,
      budget_s: payload.budget_s,
      started: new Date().toISOString(),
    };
    $('copy').disabled = false;
    $('dl').disabled = false;
    $('s-calls').textContent = '0';
    $('s-tok').textContent = '0';
    $('s-cost').textContent = '$0.0000';
    $('s-step').textContent = '–';
    $('s-el').textContent = '0s';
    $('s-answer-row').hidden = true;
    $('s-answer').textContent = '';
    Object.keys(COPIED).forEach((k) => delete COPIED[k]);
    shot.innerHTML = '<div class="empty">Starting the browser…</div>';
    $('url').textContent = '–';
    setStatus('running', 'starting');
    setPhase('', 'Starting the browser…');
    $('run').disabled = true;
    $('stop').disabled = false;
    $('clear').disabled = true;
    fpsWindow = [];
    connect(0);
    connectFrames();
  });
  $('stop').addEventListener('click', async () => {
    await fetch('/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    setPhase('undo', 'Stop requested; the loop ends at its next decision point.');
    $('stop').disabled = true;
  });

  // Clear: the stage, the run log and the replay bar go back to how the page loads; the form on the left
  // is untouched (it is what you want to run again). Disabled while a run is in progress -- clearing a
  // live run would leave its events arriving into an empty log.
  const SHOT_EMPTY = shot.innerHTML,
    PHASE_EMPTY = phaseText.textContent;
  $('clear').addEventListener('click', () => {
    fetch('/clear', { method: 'POST' }).catch(() => null);
    if (es) {
      es.close();
      es = null;
    }
    stopFrames();
    stopPlay();
    $('scrub').hidden = true;
    followFeed = true;
    newpill.classList.remove('on');
    clearInterval(timer);
    timer = null;
    startedAt = null;
    renderQ.length = 0;
    feed.innerHTML = '';
    cards = {};
    currentStep = null;
    endSeen = false;
    lastPlan = null;
    Object.keys(STORY).forEach((k) => delete STORY[k]);
    LOG = [];
    runMeta = {};
    KEYFRAMES.length = 0;
    FRAMES.length = 0;
    frameBytes = 0;
    lastFrameB64 = null;
    TL = [];
    Object.keys(COPIED).forEach((k) => delete COPIED[k]);
    $('s-calls').textContent = '0';
    $('s-tok').textContent = '0';
    $('s-cost').textContent = '$0.0000';
    $('s-step').textContent = '–';
    $('s-el').textContent = '0s';
    $('s-answer-row').hidden = true;
    $('s-answer').textContent = '';
    stageGen += 1;
    pendingB64 = null;
    shot.innerHTML = SHOT_EMPTY;
    $('url').textContent = '–';
    setStatus('', 'idle');
    setPhase('', PHASE_EMPTY);
    $('copy').disabled = true;
    $('dl').disabled = true;
    $('clear').disabled = true;
  });

  // Rejoin a run in progress, or replay the last finished one (page reload keeps the result).
  if (new URLSearchParams(window.location.search).get('test') === '1') window.__jevonly_test = { handlers };

  fetch('/status')
    .then((r) => r.json())
    .then((s) => {
      if (s.running) {
        $('run').disabled = true;
        $('stop').disabled = false;
        setStatus('running', 'running');
        connect(0);
        connectFrames();
      } else if (s.seq > 0) {
        LOG = [];
        connect(0);
        $('copy').disabled = false;
        $('dl').disabled = false;
        $('clear').disabled = false;
      }
    })
    .catch(() => {});
})();
