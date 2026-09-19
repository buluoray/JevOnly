'use strict';

const MAX_CANDIDATES = 30;
const TEXT_BUDGET = 900;

/** Normalize visible text for wire output. */
function cleanText(value) {
  return String(value || '')
    .trim()
    .replace(/\s+/g, ' ');
}

/**
 * Group text-node observations into stable page units.
 * Entries sharing a block id become one unit; ungrouped entries remain independent.
 * @param {{text:string, block?:string|null, blockText?:string}[]} entries
 * @param {number} limit maximum number of units
 * @returns {string[]}
 */
function groupTextUnits(entries, limit = 400) {
  const output = [];
  const seenBlocks = new Set();
  for (const entry of entries || []) {
    let text;
    if (entry.block) {
      if (seenBlocks.has(entry.block)) continue;
      seenBlocks.add(entry.block);
      text = entry.blockText || entry.text;
    } else {
      text = entry.text;
    }
    text = cleanText(text).slice(0, 200);
    if (text.length >= 2) output.push(text);
    if (output.length >= limit) break;
  }
  return output;
}

/**
 * Normalize a candidate description before it crosses the wire.
 * @param {object} candidate raw candidate fields
 * @returns {object}
 */
function describeCandidate(candidate) {
  const result = {
    role: cleanText(candidate.role),
    name: cleanText(candidate.name).slice(0, 80),
    ctx: cleanText(candidate.ctx),
    tag: candidate.tag || '-',
    fam: cleanText(candidate.fam).slice(0, 80),
  };
  for (const key of ['hint', 'host', 'pseudo', 'value', 'placeholder']) {
    if (candidate[key] !== undefined && candidate[key] !== '') result[key] = candidate[key];
  }
  for (const key of ['options', 'checked', 'expanded']) {
    if (candidate[key] !== undefined) result[key] = candidate[key];
  }
  return result;
}

const TRANSITION_CAP_MS = 400;

/**
 * Wait, briefly, while the page is visibly mid-transition: a menu closing, a dialog sliding in, a list
 * fading. An observation taken then shows the old state with the new one half-drawn, and a judgment on
 * it is wrong in a way no retry fixes. The wait is bounded and costs nothing on a page that is not
 * animating. Network is not waited for here (see actions.settle / wait_inflight).
 * @param {import("playwright").Page} page active page
 * @param {number} capMs longest wait
 * @returns {Promise<number>} milliseconds waited
 */
async function settleTransitions(page, capMs) {
  const started = Date.now();
  for (;;) {
    const busy = await page
      .evaluate(
        () =>
          new Promise((resolve) => {
            window.requestAnimationFrame(() => {
              const running = document.getAnimations().filter((a) => a.playState === 'running').length;
              // A focused control that says its popup is open (aria-expanded) while nothing it points at
              // (aria-controls / aria-owns) is on screen yet is mid-transition too: the suggestion list
              // is being built or faded in by script, which getAnimations() cannot see.
              const active = document.activeElement;
              let popupPending = 0;
              if (active && active.getAttribute('aria-expanded') === 'true') {
                const ids = String(active.getAttribute('aria-controls') || active.getAttribute('aria-owns') || '')
                  .split(/\s+/)
                  .filter(Boolean);
                const onScreen = (el) => {
                  if (!el) return false;
                  const r = el.getBoundingClientRect();
                  const shown =
                    typeof el.checkVisibility === 'function'
                      ? el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
                      : true;
                  return shown && r.width > 0 && r.height > 0;
                };
                if (ids.length && !ids.some((id) => onScreen(document.getElementById(id)))) popupPending = 1;
              }
              resolve(running + popupPending);
            });
          }),
      )
      .catch(() => 0);
    const elapsed = Date.now() - started;
    if (!busy || elapsed >= capMs) return elapsed;
    await page.waitForTimeout(Math.min(50, capMs - elapsed));
  }
}

/**
 * Capture the current page observation.
 * @param {import("playwright").Page} page active page
 * @param {object} opts wire-level snapshot options
 * @returns {Promise<object>} snapshot result consumed by Python
 */
async function snapshot(page, opts = {}) {
  const options = {
    maxCands: opts.max_cands || MAX_CANDIDATES,
    textBudget: opts.text_budget || TEXT_BUDGET,
    preferMain: Boolean(opts.prefer_main),
    ctxBudget: opts.ctx_budget || 140,
    viewportOnly: Boolean(opts.viewport_only),
  };
  const transition = await settleTransitions(page, opts.transition_cap_ms ?? TRANSITION_CAP_MS);

  const info = await page.evaluate((o) => {
    const clean = (value) =>
      String(value || '')
        .trim()
        .replace(/\s+/g, ' ');
    const headings = [...document.querySelectorAll('h1,h2,h3')]
      .map((heading) => clean(heading.innerText))
      .filter(Boolean)
      .slice(0, 8);
    const selector =
      'a[href],button,input,select,textarea,[role=button],[role=link],[role=tab],[role=menuitem],' +
      '[role=checkbox],[role=radio],[role=combobox],[role=searchbox],[role=option]';
    const candidates = [];
    for (const element of document.querySelectorAll('[data-jev-cand]')) {
      element.removeAttribute('data-jev-cand');
    }

    let hits = [];
    for (const element of document.querySelectorAll(selector)) {
      const rect = element.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0 || element.type === 'hidden') continue;
      if (
        element.matches(':disabled') ||
        element.getAttribute('aria-disabled') === 'true' ||
        element.closest('[aria-disabled="true"]')
      ) {
        continue;
      }
      if (o.viewportOnly) {
        const centerX = rect.x + rect.width / 2;
        const centerY = rect.y + rect.height / 2;
        if (centerX < 0 || centerY < 0 || centerX >= window.innerWidth || centerY >= window.innerHeight) continue;
      }
      hits.push(element);
    }

    // A dialog that is in the DOM but not on screen (display:none, visibility:hidden, opacity 0 while it
    // fades) is not open: scoping the candidates to it would offer controls nobody can click.
    const isShown = (element) => {
      if (typeof element.checkVisibility === 'function') {
        // ancestor-aware: an opacity-0 or hidden ancestor hides this element too
        return element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
      }
      const style = window.getComputedStyle(element);
      return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0.05;
    };
    const area = (element) => {
      if (!isShown(element)) return 0;
      const rect = element.getBoundingClientRect();
      return Math.max(0, rect.width) * Math.max(0, rect.height);
    };
    const viewportArea = window.innerWidth * window.innerHeight;
    const modals = [...document.querySelectorAll('[aria-modal="true"],[role=dialog],dialog[open]')].filter(
      (dialog) =>
        area(dialog) > 0 &&
        (dialog.getAttribute('aria-modal') === 'true' ||
          dialog.tagName === 'DIALOG' ||
          area(dialog) >= viewportArea * 0.33),
    );
    const activeElement = document.activeElement;
    const holder =
      activeElement && activeElement !== document.body
        ? activeElement.closest('[role=dialog],dialog,[aria-modal="true"],[role=listbox]')
        : null;
    if (holder && area(holder) > 0 && !modals.includes(holder)) modals.push(holder);
    // A popup does not have to be big or aria-modal to be the thing the user is looking at: the listbox
    // a focused combobox points at (aria-controls / aria-owns), or any shown dialog that holds visible
    // options, is where the next click belongs. Size alone misjudges a suggestion list that is a few
    // rows tall, and then the whole page competes with it for the pick.
    const owned =
      activeElement && (activeElement.getAttribute('aria-controls') || activeElement.getAttribute('aria-owns'));
    for (const id of String(owned || '')
      .split(/\s+/)
      .filter(Boolean)) {
      const target = document.getElementById(id);
      const popup =
        target &&
        (target.closest('[role=dialog],dialog,[aria-modal="true"]') || target.closest('[role=listbox]') || target);
      if (popup && area(popup) > 0 && !modals.includes(popup)) modals.push(popup);
    }
    for (const dialog of document.querySelectorAll('[role=dialog],dialog[open]')) {
      if (modals.includes(dialog) || area(dialog) === 0) continue;
      if ([...dialog.querySelectorAll('[role=option]')].some((option) => area(option) > 0)) modals.push(dialog);
    }

    let modal = null;
    let scopeElement = document.body;
    if (modals.length) {
      const top = modals.reduce((left, right) => (area(left) >= area(right) ? left : right));
      const inside = hits.filter((element) => top.contains(element));
      if (inside.length) {
        hits = inside;
        scopeElement = top;
        const label = top.getAttribute('aria-label') || (top.querySelector('h1,h2,h3') || {}).innerText || '';
        modal = clean(label).slice(0, 80) || '(unlabelled dialog)';
      }
    }

    const runMinimum = 10;
    const family = (element) =>
      `${element.tagName.toLowerCase()}|${element.getAttribute('role') || ''}|${[...element.classList]
        .slice(0, 2)
        .join(' ')}`;
    const runOf = new Array(hits.length).fill(-1);
    const runs = [];
    for (let index = 0; index < hits.length;) {
      let end = index;
      const currentFamily = family(hits[index]);
      while (end + 1 < hits.length && family(hits[end + 1]) === currentFamily) end += 1;
      if (end - index + 1 >= runMinimum) {
        runs.push({ start: index, end, fam: currentFamily, shown: 0 });
        for (let member = index; member <= end; member += 1) runOf[member] = runs.length - 1;
      }
      index = end + 1;
    }
    const inChrome = (element) =>
      Boolean(element.closest('nav,header,footer,[role=navigation],[role=banner],[role=contentinfo]'));
    const singles = hits.filter((_element, index) => runOf[index] < 0);
    const members = hits.filter((_element, index) => runOf[index] >= 0);
    const ordered = o.preferMain
      ? [...singles.filter((element) => !inChrome(element)), ...members, ...singles.filter(inChrome)]
      : [...singles, ...members];

    const nameOf = (element, tag, isField) => {
      const label =
        element.labels && element.labels.length
          ? clean([...element.labels].map((item) => item.innerText).join(' '))
          : '';
      const child = element.querySelector('[aria-label],img[alt]');
      const childName = clean(child ? child.getAttribute('aria-label') || child.getAttribute('alt') : '');
      let name = clean(
        element.getAttribute('aria-label') ||
          label ||
          element.placeholder ||
          (isField ? '' : element.innerText) ||
          (isField ? '' : element.value) ||
          element.name ||
          element.getAttribute('title') ||
          element.getAttribute('data-tooltip') ||
          (tag === 'select' ? 'select' : ''),
      );
      if (!name) name = childName;
      else if (childName && !element.getAttribute('aria-label') && childName !== name && !name.includes(childName)) {
        name = `${name} (${childName})`;
      }
      return name.slice(0, 80);
    };

    for (const element of ordered.slice(0, o.maxCands)) {
      const tag = element.tagName.toLowerCase();
      const role =
        element.getAttribute('role') ||
        { a: 'link', button: 'button', select: 'combobox', textarea: 'textbox' }[tag] ||
        (tag === 'input'
          ? { submit: 'button', button: 'button', checkbox: 'checkbox', radio: 'radio', search: 'searchbox' }[
              element.type
            ] || 'textbox'
          : tag);
      const isField =
        (tag === 'input' && !['submit', 'button', 'checkbox', 'radio'].includes(element.type)) ||
        tag === 'textarea' ||
        tag === 'select';
      const name = nameOf(element, tag, isField);
      let context = '';
      const row = element.closest('tr,li,fieldset,label');
      if (row && row !== element) context = clean(row.innerText).slice(0, o.ctxBudget);
      const extra = { tag, fam: family(element).slice(0, 80) };
      const descriptionIds = (element.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
      const described =
        descriptionIds
          .map((id) => {
            const description = document.getElementById(id);
            return description ? clean(description.innerText || description.textContent) : '';
          })
          .filter(Boolean)
          .join(' ') ||
        clean(element.getAttribute('aria-description') || '') ||
        (element.getAttribute('title') && clean(element.getAttribute('title')) !== name
          ? clean(element.getAttribute('title'))
          : '');
      if (described) extra.hint = described.slice(0, 120);
      if (tag === 'a' && element.href) {
        try {
          extra.host = new URL(element.href).hostname.replace(/^www\./, '');
        } catch (_error) {
          // Ignore non-URL href values.
        }
      }
      const runIndex = runOf[hits.indexOf(element)];
      if (runIndex >= 0) runs[runIndex].shown += 1;
      if (tag === 'select') {
        extra.options = [...element.options].map((option) => option.text.trim()).slice(0, 12);
        extra.value = element.selectedOptions[0] ? element.selectedOptions[0].text.trim() : '';
      }
      if (element.type === 'checkbox' || element.type === 'radio') extra.checked = element.checked;
      if (
        (tag === 'input' || tag === 'textarea') &&
        element.value &&
        !['submit', 'button', 'checkbox', 'radio'].includes(element.type)
      ) {
        extra.value = clean(element.value).slice(0, 40);
      }
      if (element.placeholder) extra.placeholder = element.placeholder;
      if (element.hasAttribute('aria-expanded')) extra.expanded = element.getAttribute('aria-expanded') === 'true';
      candidates.push({ role, name, ctx: context, ...extra });
      element.setAttribute('data-jev-cand', String(candidates.length - 1));
    }

    const shown = Math.min(ordered.length, o.maxCands);
    const truncatedRuns = runs
      .filter((run) => run.shown < run.end - run.start + 1)
      .map((run) => {
        const firstElement = hits[run.start];
        const lastElement = hits[run.end];
        const firstTag = firstElement.tagName.toLowerCase();
        const lastTag = lastElement.tagName.toLowerCase();
        return {
          role: firstElement.getAttribute('role') || { a: 'link', button: 'button' }[firstTag] || firstTag,
          total: run.end - run.start + 1,
          shown: run.shown,
          first: nameOf(firstElement, firstTag, false),
          last: nameOf(lastElement, lastTag, false),
        };
      });

    let body;
    const unitEntries = [];
    if (o.viewportOnly) {
      const blockSelector =
        'tr,[role=row],li,[role=listitem],[role=option],[role=gridcell],article,p,h1,h2,h3,h4,h5,h6,' +
        'dt,dd,blockquote,figcaption,label,summary';
      const shortBlock = 200;
      const walker = document.createTreeWalker(scopeElement, NodeFilter.SHOW_TEXT);
      const range = document.createRange();
      const words = [];
      const blockIds = new Map();
      let blockCounter = 0;
      let node;
      let length = 0;
      while ((node = walker.nextNode()) && length < o.textBudget) {
        const value = node.textContent.trim();
        const parent = node.parentElement;
        if (!value || !parent || parent.closest('script,style,noscript,template')) continue;
        range.selectNodeContents(node);
        const rect = range.getBoundingClientRect();
        if (!(
          rect.width > 0 &&
          rect.height > 0 &&
          rect.bottom > 0 &&
          rect.top < window.innerHeight &&
          rect.right > 0 &&
          rect.left < window.innerWidth
        )) {
          continue;
        }
        length += value.length + 1;
        words.push(value);
        const block = parent.closest(blockSelector);
        if (block && scopeElement.contains(block)) {
          const blockText = clean(block.innerText);
          if (blockText.length <= shortBlock) {
            if (!blockIds.has(block)) blockIds.set(block, `block-${blockCounter++}`);
            unitEntries.push({ text: value, block: blockIds.get(block), blockText });
            continue;
          }
        }
        unitEntries.push({ text: value, block: null });
      }
      body = clean(words.join(' ')).slice(0, o.textBudget);
      const scrollingElement = document.scrollingElement || document.documentElement;
      const canScrollDown = scrollingElement.scrollTop + window.innerHeight < scrollingElement.scrollHeight - 2;
      const canScrollUp = scrollingElement.scrollTop > 2;
      if (canScrollDown) {
        candidates.push({
          role: 'scroll',
          name: 'Scroll down to see more of the page',
          ctx: '',
          tag: '-',
          fam: 'scroll',
          pseudo: 'down',
        });
      }
      if (canScrollUp) {
        candidates.push({ role: 'scroll', name: 'Scroll up', ctx: '', tag: '-', fam: 'scroll', pseudo: 'up' });
      }
    } else {
      body = clean(scopeElement.innerText).slice(0, o.textBudget);
      for (const [index, text] of String(scopeElement.innerText || '')
        .split(/\n+/)
        .map((value) => value.trim())
        .filter(Boolean)
        .entries()) {
        unitEntries.push({ text, block: `line-${index}`, blockText: text });
      }
    }

    return {
      headings,
      candidates,
      body,
      unitEntries,
      total: hits.length,
      modal,
      omitted: hits.length - shown,
      truncatedRuns,
    };
  }, options);

  const dialogs = page._jevDialogs ? page._jevDialogs.splice(0) : [];
  return {
    transition_ms: transition,
    url: page.url(),
    title: await page.title(),
    headings: info.headings,
    visible_text: info.body,
    text_units: groupTextUnits(info.unitEntries),
    candidates: info.candidates.map(describeCandidate),
    dialogs,
    total_interactive: info.total,
    modal: info.modal,
    omitted: info.omitted,
    truncated_runs: info.truncatedRuns,
  };
}

module.exports = {
  settleTransitions,
  MAX_CANDIDATES,
  TEXT_BUDGET,
  cleanText,
  describeCandidate,
  groupTextUnits,
  snapshot,
};
