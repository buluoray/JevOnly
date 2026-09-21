'use strict';

const net = require('net');
const { semanticSignature } = require('./signature');

const IGNORED_REQUEST_TYPES = new Set(['websocket', 'eventsource', 'ping']);

/**
 * Track page requests so settle waits only for requests started by the current action.
 * @param {() => number} now clock function
 * @returns {object} request tracker
 */
function createRequestTracker(now = Date.now) {
  const inflight = new Map();
  return {
    attach(page) {
      page.on('request', (request) => inflight.set(request, now()));
      page.on('requestfinished', (request) => inflight.delete(request));
      page.on('requestfailed', (request) => inflight.delete(request));
    },
    start(request, startedAt = now()) {
      inflight.set(request, startedAt);
    },
    finish(request) {
      inflight.delete(request);
    },
    pendingSince(actionStartedAt) {
      let pending = 0;
      for (const [request, startedAt] of inflight) {
        if (startedAt < actionStartedAt - 50) continue;
        if (IGNORED_REQUEST_TYPES.has(request.resourceType())) continue;
        pending += 1;
      }
      return pending;
    },
    get size() {
      return inflight.size;
    },
  };
}

/**
 * Wait for a fixed floor, then only for action-triggered requests, bounded by a cap.
 * @param {import("playwright").Page} page active page
 * @param {object} tracker request tracker
 * @param {number} actionStartedAt action start timestamp
 * @param {number} floorMs minimum fixed wait
 * @param {number} capMs total wait cap
 * @param {() => number} now clock function
 * @returns {Promise<{settled_ms:number, settled:boolean}>}
 */
async function settle(page, tracker, actionStartedAt, floorMs, capMs, now = Date.now) {
  const started = now();
  if (floorMs) await page.waitForTimeout(floorMs);
  for (;;) {
    const elapsed = now() - started;
    if (tracker.pendingSince(actionStartedAt) === 0) return { settled_ms: elapsed, settled: true };
    if (elapsed >= capMs) return { settled_ms: elapsed, settled: false };
    await page.waitForTimeout(Math.min(100, Math.max(1, capMs - elapsed)));
  }
}

/** Browser actions and state shared by the JSON-lines protocol. */

/**
 * First line of a Playwright error plus the reason from its call log ("element is not stable",
 * "<div> intercepts pointer events", "waiting for element to be visible"): the bare "Timeout 3500ms
 * exceeded" said nothing about why a visible menu option would not take a click.
 */
function describeActionError(error) {
  const text = String(error.message || error);
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  const head = (lines[0] || '').slice(0, 120);
  const reason = lines
    .slice(1)
    .filter((l) =>
      /not stable|intercepts pointer|not visible|outside of the viewport|waiting for|not enabled|detached/i.test(l),
    )
    .pop();
  return reason ? `${head} (${reason.replace(/^-\s*/, '').slice(0, 120)})` : head.slice(0, 160);
}

class BrowserActions {
  /** @param {object} environment result from launchBrowser */
  constructor(environment) {
    Object.assign(this, environment);
    this.page = environment.page;
    this.tabs = [this.page];
    this.opened = [];
    this.actionStartedAt = 0;
    this.closing = false;
    this.tracker = createRequestTracker();
    this.socket = null;
    this.cdp = null;
  }

  /** Attach listeners and optional screencast transport. */
  async init() {
    if (process.env.JEV_FRAMES) {
      this.socket = net.createConnection(process.env.JEV_FRAMES);
      this.socket.on('error', () => {
        this.socket = null;
      });
      this.socket.on('close', () => {
        this.socket = null;
      });
    }
    this.context.on('page', (page) => this.opened.push({ page, timestamp: Date.now() }));
    await this.attach(this.page);
  }

  /** @returns {import("playwright").Page} current active page */
  currentPage() {
    return this.page;
  }

  /** Attach JevOnly listeners to a page and make it active. */
  async attach(page) {
    this.page = page;
    if (!page._jevDialogs) {
      page._jevDialogs = [];
      this.tracker.attach(page);
      page.on('dialog', async (dialog) => {
        page._jevDialogs.push(`${dialog.type()}: ${dialog.message().slice(0, 200)}`);
        await dialog.dismiss().catch(() => null);
      });
      page.on('close', async () => {
        const index = this.tabs.indexOf(page);
        if (index >= 0) this.tabs.splice(index, 1);
        if (!this.closing && this.page === page && this.tabs.length) {
          await this.attach(this.tabs[this.tabs.length - 1]).catch(() => null);
        }
      });
    }
    await page.bringToFront().catch(() => null);
    await this.startScreencast(page);
  }

  /** Start the optional frame stream for a page. */
  async startScreencast(page) {
    if (!process.env.JEV_FRAMES) return;
    try {
      if (this.cdp) {
        await this.cdp.detach().catch(() => null);
        this.cdp = null;
      }
      this.cdp = await this.context.newCDPSession(page);
      this.cdp.on('Page.screencastFrame', (frame) => {
        if (this.socket) {
          this.socket.write(
            `${JSON.stringify({
              jpeg_b64: frame.data,
              w: frame.metadata.deviceWidth,
              h: frame.metadata.deviceHeight,
              scale: frame.metadata.pageScaleFactor,
              sy: frame.metadata.scrollOffsetY,
              top: frame.metadata.offsetTop,
              ts: Date.now(),
            })}\n`,
          );
        }
        this.cdp.send('Page.screencastFrameAck', { sessionId: frame.sessionId }).catch(() => null);
      });
      await this.cdp.send('Page.startScreencast', {
        format: 'jpeg',
        quality: 80,
        maxWidth: 1280,
        maxHeight: 900,
        everyNthFrame: 1,
      });
    } catch (error) {
      process.stderr.write(`screencast unavailable: ${String(error).slice(0, 200)}\n`);
    }
  }

  /** Remove a temporary candidate highlight. */
  async clearHighlight() {
    await this.page
      .evaluate(() => {
        for (const element of document.querySelectorAll('[data-jev-hl]')) {
          element.style.cssText = element.getAttribute('data-jev-hl');
          element.removeAttribute('data-jev-hl');
        }
      })
      .catch(() => null);
  }

  /** Adopt the first tab opened by the current action and close extra popups. */
  async adoptNewTab(settleMs) {
    const fresh = this.opened.filter((entry) => entry.timestamp >= this.actionStartedAt);
    const stale = this.opened.filter((entry) => entry.timestamp < this.actionStartedAt);
    this.opened.length = 0;
    for (const entry of stale) await entry.page.close().catch(() => null);
    if (!fresh.length) return false;
    const page = fresh[0].page;
    for (const entry of fresh.slice(1)) await entry.page.close().catch(() => null);
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => null);
    if (page.isClosed()) return false;
    if (this.headless) await page.setViewportSize(this.viewport).catch(() => null);
    this.tabs.push(page);
    await this.attach(page);
    await page.waitForTimeout(settleMs);
    return true;
  }

  /** Navigate to a URL. */
  async goto(command) {
    this.actionStartedAt = Date.now();
    await this.page.goto(command.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    return {
      ok: true,
      ...(await settle(
        this.page,
        this.tracker,
        this.actionStartedAt,
        command.settle_ms ?? 0,
        command.settle_cap_ms ?? 0,
      )),
    };
  }

  /** Execute an indexed click/fill/select/check action, or a scroll/find pseudo-action. */
  async act(command) {
    if (command.action === 'scroll') return this.scroll(command);
    if (command.action === 'find') return this.find(command);

    const locator = this.page.locator(`[data-jev-cand="${command.index}"]`);
    if ((await locator.count()) === 0) return { ok: false, error: 'stale candidate: the control left the page' };
    if (command.expect_sig) {
      // The control is still the same DOM node, but is it still the same control? A list that re-rendered
      // in place, a button whose label flipped, a field a script filled: the observation that chose it is
      // gone, so the action is refused UNSENT (nothing to undo) and the loop looks again.
      const now = await locator
        .first()
        .evaluate(semanticSignature)
        .catch(() => null);
      if (now !== null && now !== command.expect_sig) {
        const was = String(command.expect_sig).split('|')[2] || '';
        const is = String(now).split('|')[2] || '';
        return {
          ok: false,
          error: `stale candidate: target changed since observation (was ${JSON.stringify(was.slice(0, 40))}, now ${JSON.stringify(is.slice(0, 40))})`,
        };
      }
    }
    this.actionStartedAt = Date.now();
    let recovered = null;
    try {
      if (command.action === 'click') {
        try {
          await locator.first().click({ timeout: 2000 });
        } catch (error) {
          const message = String(error.message || error);
          if (!/Timeout|intercepts pointer events|not visible|outside of the viewport/i.test(message)) throw error;
          // A slow first click usually means the target is still animating into place (a menu that grows
          // open, a dialog that fades in). Escape used to be the recovery -- but when the target sits INSIDE
          // the open dialog, Escape closes that dialog and the option is gone: "One way" was pressed, the
          // menu vanished, the state changed, and the loop rejected the option for good. So: inside a popup,
          // wait and click again with more time; outside one, Escape whatever overlay is in the way first.
          const inPopup = await locator
            .first()
            .evaluate((el) =>
              Boolean(el.closest('[role=dialog],dialog,[aria-modal="true"],[role=listbox],[role=menu]')),
            )
            .catch(() => false);
          if (inPopup) {
            await this.page.waitForTimeout(400);
            if ((await locator.count()) === 0) throw error;
            try {
              await locator.first().click({ timeout: 3500 });
              recovered = 'waited';
            } catch (again) {
              // Still timing out inside the popup: Playwright's actionability check (stable, receives
              // pointer events) keeps failing while the option is plainly on screen -- on a Mac the
              // "One way" option timed out twice, was rejected at that state, and the run died with the
              // menu still open. The element is the one the model chose and it is in the open popup, so
              // skip the check: a forced click, then a synthetic click as the last resort.
              if (
                !/Timeout|intercepts pointer events|not visible|outside of the viewport/i.test(
                  String(again.message || again),
                )
              )
                throw again;
              if ((await locator.count()) === 0) throw again;
              // The forced click skips only the stability / pointer-events check. A target that is not
              // visible or is disabled is not clicked by any means: a synthetic click on such an element
              // would activate something the user could not, so the error stands and the loop re-plans.
              const target = locator.first();
              const usable =
                (await target.isVisible().catch(() => false)) && (await target.isEnabled().catch(() => false));
              if (!usable) throw again;
              await target.click({ force: true, timeout: 2000 });
              recovered = 'forced';
            }
          } else {
            await this.page.keyboard.press('Escape').catch(() => null);
            await this.page.waitForTimeout(350);
            if ((await locator.count()) === 0) throw error;
            await locator.first().click({ timeout: 2000 });
            recovered = 'escape';
          }
        }
      } else if (command.action === 'fill' || command.action === 'fill_enter') {
        await this.fill(locator.first(), command);
      } else if (command.action === 'select') {
        await locator.first().selectOption({ label: String(command.value) }, { timeout: 8000 });
      } else if (command.action === 'check') {
        await locator.first().setChecked(true, { timeout: 8000 });
      }
    } catch (error) {
      await this.clearHighlight();
      // A target that is no longer on screen when the click is attempted is the same case as a changed
      // signature: the observation that offered it is gone (its menu closed, its list re-rendered), and
      // nothing was sent. Say "stale" so the loop re-observes instead of verifying a no-op and holding
      // the miss against a control that was never there to click.
      const gone =
        /not visible|detached|outside of the viewport/i.test(String(error.message || error)) &&
        !(await locator
          .first()
          .isVisible()
          .catch(() => false));
      if (gone) return { ok: false, error: 'stale candidate: target is no longer visible at dispatch time' };
      return {
        ok: false,
        error: `action failed: ${describeActionError(error)}`,
      };
    }

    await this.page.waitForLoadState('domcontentloaded').catch(() => null);
    await this.clearHighlight();
    const settled = await settle(
      this.page,
      this.tracker,
      this.actionStartedAt,
      command.act_settle_ms ?? 0,
      command.settle_cap_ms ?? 0,
    );
    const newTab = await this.adoptNewTab(0);
    return { ok: true, url: this.page.url(), new_tab: newTab, recovered, ...settled };
  }

  /** How many requests started by the last action are still in flight. */
  inflight() {
    return { ok: true, pending: this.tracker.pendingSince(this.actionStartedAt) };
  }

  /** Wait, up to cap_ms, while requests started by the last action are still in flight. */
  async waitInflight(command) {
    const settled = await settle(this.page, this.tracker, this.actionStartedAt, 0, command.cap_ms ?? 3000);
    return { ok: true, pending: this.tracker.pendingSince(this.actionStartedAt), ...settled };
  }

  /** Fill a normal field or autocomplete combobox. */
  async fill(element, command) {
    const isCombo = (await element.getAttribute('role')) === 'combobox';
    if (isCombo) {
      await element.click({ timeout: 8000 });
      await this.page.waitForTimeout(250);
      await this.page.keyboard.press('ControlOrMeta+A').catch(() => null);
      await this.page.keyboard.type(String(command.value), { delay: 40 });
      const option = this.page.locator('[role=listbox] [role=option], [role=option]').first();
      await option.waitFor({ state: 'visible', timeout: 2500 }).catch(() => null);
      if (command.action === 'fill_enter') {
        if (await option.isVisible().catch(() => false)) await option.click({ timeout: 8000 });
        else await this.page.keyboard.press('Enter');
      }
      return;
    }
    await element.fill(String(command.value), { timeout: 8000 });
    if (command.action === 'fill_enter') await element.press('Enter');
  }

  /** Scroll by most of one viewport. */
  async scroll(command) {
    this.actionStartedAt = Date.now();
    const viewportHeight =
      (this.page.viewportSize() || {}).height || (await this.page.evaluate(() => window.innerHeight).catch(() => 900));
    const delta = Math.round(viewportHeight * 0.8) * (command.value === 'up' ? -1 : 1);
    await this.page.mouse.wheel(0, delta);
    const settled = await settle(
      this.page,
      this.tracker,
      this.actionStartedAt,
      command.act_settle_ms ?? 0,
      command.settle_cap_ms ?? 0,
    );
    return { ok: true, url: this.page.url(), ...settled };
  }

  /** Find text in the page and center an occurrence. */
  async find(command) {
    this.actionStartedAt = Date.now();
    const found = await this.page.evaluate((text) => {
      const query = String(text).toLowerCase();
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const hits = [];
      let node;
      while ((node = walker.nextNode())) {
        if (!node.textContent.toLowerCase().includes(query)) continue;
        const element = node.parentElement;
        if (!element || element.closest('script,style,noscript,template')) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        hits.push({ element, onScreen: rect.top >= 0 && rect.bottom <= window.innerHeight });
      }
      const pick = hits.find((hit) => !hit.onScreen) || hits[0];
      if (!pick) return false;
      pick.element.scrollIntoView({ block: 'center' });
      return true;
    }, command.value || '');
    const settled = await settle(
      this.page,
      this.tracker,
      this.actionStartedAt,
      command.act_settle_ms ?? 0,
      command.settle_cap_ms ?? 0,
    );
    if (!found) {
      return {
        ok: false,
        error: `action failed: "${String(command.value).slice(0, 60)}" is not on this page`,
      };
    }
    return { ok: true, url: this.page.url(), found: true, ...settled };
  }

  /** Type one key or short text into the focused field. */
  async keyboard(command) {
    if (command.focus && Number.isInteger(command.index)) {
      const locator = this.page.locator(`[data-jev-cand="${command.index}"]`);
      if ((await locator.count()) === 0) return { ok: false, error: 'stale candidate' };
      await locator
        .first()
        .click({ timeout: 8000 })
        .catch(() => null);
      await this.page
        .waitForFunction(
          () => {
            const active = document.activeElement;
            return Boolean(
              active &&
              (active.isContentEditable ||
                (typeof active.value === 'string' && /^(INPUT|TEXTAREA)$/.test(active.tagName))),
            );
          },
          null,
          { timeout: 1500 },
        )
        .catch(() => null);
      const caret = await this.page
        .evaluate(() => {
          const active = document.activeElement;
          if (!active || typeof active.value !== 'string' || !active.value) return 'none';
          return active.selectionStart === 0 && active.selectionEnd === active.value.length ? 'selected' : 'text';
        })
        .catch(() => 'none');
      // Move the caret to the end INSIDE the focused field, never with a page-level End key: a site that
      // re-homes focus into a popup right after the click leaves a window where the active element is
      // <body>, and End pressed then scrolls the whole page to the bottom.
      if (caret === 'text') {
        await this.page
          .evaluate(() => {
            const active = document.activeElement;
            if (active && typeof active.value === 'string' && typeof active.setSelectionRange === 'function') {
              const end = active.value.length;
              try {
                active.setSelectionRange(end, end);
              } catch (e) {
                /* email/number inputs refuse setSelectionRange: the caret stays where the click put it */
              }
            }
          })
          .catch(() => null);
      }
    }
    this.actionStartedAt = Date.now();
    if (command.key) await this.page.keyboard.press(command.key);
    else if (command.text) await this.page.keyboard.type(String(command.text), { delay: 30 });
    await settle(this.page, this.tracker, this.actionStartedAt, 0, command.settle_ms ?? 400);
    const info = await this.page
      .evaluate(() => {
        const clean = (value) =>
          String(value || '')
            .trim()
            .replace(/\s+/g, ' ');
        const visible = (element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        };
        const active = document.activeElement;
        const isText = active && typeof active.value === 'string';
        const typed = isText ? active.value : active && active.isContentEditable ? clean(active.innerText) : '';
        const cursor = isText && typeof active.selectionStart === 'number' ? active.selectionStart : null;
        const selectionEnd = isText && typeof active.selectionEnd === 'number' ? active.selectionEnd : null;
        const options = [...document.querySelectorAll('[role=option]')]
          .filter(visible)
          .slice(0, 8)
          .map((element) => clean(element.innerText).slice(0, 90));
        return { typed, cursor, sel_end: selectionEnd, options };
      })
      .catch(() => ({ typed: '', cursor: null, sel_end: null, options: [] }));
    return { ok: true, ...info };
  }

  /** Apply the partial-fill fault used by the harness. */
  async truncateFill(command) {
    const locator = this.page.locator(`[data-jev-cand="${command.index}"]`);
    if ((await locator.count()) === 0) return { ok: false, error: 'stale candidate' };
    const text = String(command.value);
    const part = text.slice(0, Math.max(1, Math.floor(text.length * 0.6)));
    this.actionStartedAt = Date.now();
    try {
      await locator.first().fill(part, { timeout: 8000 });
      if (command.action === 'fill_enter') await locator.first().press('Enter');
    } catch (error) {
      await this.clearHighlight();
      return {
        ok: false,
        error: `action failed: ${describeActionError(error)}`,
      };
    }
    await this.page.waitForLoadState('domcontentloaded').catch(() => null);
    await this.clearHighlight();
    const settled = await settle(
      this.page,
      this.tracker,
      this.actionStartedAt,
      command.act_settle_ms ?? 0,
      command.settle_cap_ms ?? 0,
    );
    const newTab = await this.adoptNewTab(0);
    return { ok: true, landed: part, url: this.page.url(), new_tab: newTab, ...settled };
  }

  /** Add the unrelated-overlay fault used by the harness. */
  async injectPopup() {
    await this.page
      .evaluate(() => {
        if (document.getElementById('jev-unrelated-overlay')) return;
        const box = document.createElement('div');
        box.id = 'jev-unrelated-overlay';
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-label', 'Special offer');
        box.style.cssText =
          'position:fixed;left:50%;top:16px;transform:translateX(-50%);z-index:99999;' +
          'background:#fff;border:2px solid #c33;padding:14px 18px;font:14px sans-serif;' +
          'box-shadow:0 4px 18px rgba(0,0,0,.3)';
        box.innerHTML =
          '<p id="jev-overlay-text">Join our newsletter for 20% off your next order!</p>' +
          '<input id="jev-overlay-email" type="email" placeholder="you@example.com" aria-label="Email address">' +
          '<button id="jev-overlay-sub" type="button">Subscribe</button>' +
          '<button id="jev-overlay-no" type="button" onclick="document.getElementById(\'jev-unrelated-overlay\').remove()">No thanks</button>';
        document.body.appendChild(box);
      })
      .catch(() => null);
    await this.page.waitForTimeout(200);
    return { ok: true };
  }

  /** Capture a viewport JPEG, optionally highlighting one candidate. */
  async screenshot(command) {
    const highlight = Number.isInteger(command.highlight) ? command.highlight : null;
    if (highlight !== null) {
      await this.page
        .evaluate((index) => {
          const element = document.querySelector(`[data-jev-cand="${index}"]`);
          if (!element) return;
          element.setAttribute('data-jev-hl', element.style.cssText || '');
          element.style.outline = '3px solid #ff3d00';
          element.style.outlineOffset = '2px';
          element.style.boxShadow = '0 0 0 6px rgba(255,61,0,.25)';
          // Scroll only a target that is off screen. This screenshot is taken right before the action, and
          // scrolling a page with a menu open closes the menu: Google Flights' "One way" option was on
          // screen when observed, the highlight scrolled it "into view", the menu shut, and the click that
          // followed found nothing visible -- on every machine, in one run out of three.
          const rect = element.getBoundingClientRect();
          const onScreen =
            rect.top >= 0 && rect.left >= 0 && rect.bottom <= window.innerHeight && rect.right <= window.innerWidth;
          if (!onScreen) {
            try {
              element.scrollIntoView({ block: 'center', inline: 'nearest' });
            } catch (_error) {
              // A detached element is harmless for a diagnostic screenshot.
            }
          }
        }, highlight)
        .catch(() => null);
    }
    const buffer = await this.page
      .screenshot({ type: 'jpeg', quality: command.quality || 80, timeout: 8000 })
      .catch(() => null);
    if (highlight !== null && !command.keep) await this.clearHighlight();
    return buffer
      ? { ok: true, jpeg_b64: buffer.toString('base64'), url: this.page.url() }
      : { ok: false, error: 'screenshot failed' };
  }

  /** Undo navigation or close an adopted tab. */
  async back() {
    if (this.tabs.length > 1 && this.tabs[this.tabs.length - 1] === this.page) {
      const gone = this.tabs.pop();
      await this.attach(this.tabs[this.tabs.length - 1]);
      await gone.close().catch(() => null);
      return { ok: true, url: this.page.url(), closed_tab: true };
    }
    this.actionStartedAt = Date.now();
    await this.page.goBack({ waitUntil: 'domcontentloaded', timeout: 5000 }).catch(() => null);
    return { ok: true, url: this.page.url() };
  }

  /** Report browser-visible automation diagnostics. */
  async probe() {
    const info = await this.page.evaluate(() => ({
      webdriver: navigator.webdriver,
      ua: navigator.userAgent,
      cookies: document.cookie ? document.cookie.split(';').length : 0,
    }));
    return {
      ok: true,
      ...info,
      headless: this.headless,
      profile: Boolean(this.profileDir),
      channel: this.channel || 'chromium',
    };
  }

  /** Close the browser with a bounded profile flush. */
  async close() {
    this.closing = true;
    await Promise.race([this.browser.close().catch(() => null), new Promise((resolve) => setTimeout(resolve, 5000))]);
  }
}

module.exports = { BrowserActions, IGNORED_REQUEST_TYPES, createRequestTracker, settle };
