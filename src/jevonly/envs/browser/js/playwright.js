'use strict';

const path = require('path');

/**
 * Return Playwright module candidates in resolution order.
 * @param {NodeJS.ProcessEnv} env process environment
 * @param {string} moduleDir directory containing this module
 * @returns {string[]} module names or absolute paths
 */
function playwrightCandidates(env = process.env, moduleDir = __dirname) {
  return [
    env.JEV_PLAYWRIGHT,
    path.join(moduleDir, 'node_modules', 'playwright'),
    path.join(moduleDir, '..', 'node_modules', 'playwright'),
    path.resolve(moduleDir, '..', '..', '..', 'node_modules', 'playwright'),
    'playwright',
  ].filter(Boolean);
}

/**
 * Locate and load Playwright.
 * @param {NodeJS.ProcessEnv} env process environment
 * @returns {import("playwright")} Playwright module
 */
function loadPlaywright(env = process.env) {
  const failures = [];
  for (const candidate of playwrightCandidates(env)) {
    try {
      return require(candidate);
    } catch (error) {
      failures.push(`${candidate}: ${String(error.message || error).split('\n')[0]}`);
    }
  }
  const error = new Error('playwright not found; install dependencies with `npm install`');
  error.attempts = failures;
  throw error;
}

/**
 * Launch Chromium using JevOnly's environment-controlled browser settings.
 * @param {NodeJS.ProcessEnv} env process environment
 * @returns {Promise<object>} browser handles and launch metadata
 */
async function launchBrowser(env = process.env) {
  const { chromium } = loadPlaywright(env);
  const channel = env.JEV_CHROME_CHANNEL || undefined;
  const profileDir = env.JEV_PROFILE_DIR || '';
  const headless = env.JEV_HEADED !== '1';
  const viewport = { width: 1280, height: 900 };
  const launchOptions = {
    headless,
    ignoreDefaultArgs: ['--enable-automation'],
    args: headless ? ['--disable-blink-features=AutomationControlled'] : ['--window-size=1280,980'],
    chromiumSandbox: Boolean(channel) || process.platform === 'darwin',
  };
  const viewportOption = headless ? viewport : null;

  async function open(options) {
    if (profileDir) {
      return chromium.launchPersistentContext(profileDir, { ...options, viewport: viewportOption });
    }
    return chromium.launch(options);
  }

  let browser;
  if (channel) {
    try {
      browser = await open({ ...launchOptions, channel });
    } catch (error) {
      process.stderr.write(
        `chrome channel "${channel}" unavailable (${String(error.message || error).split('\n')[0]}); using bundled chromium\n`,
      );
    }
  }
  if (!browser) browser = await open(launchOptions);

  const page = profileDir
    ? browser.pages()[0] || (await browser.newPage())
    : await browser.newPage({ viewport: viewportOption });
  if (profileDir && headless) await page.setViewportSize(viewport);
  const context = page.context();

  if (!headless) {
    const hideWebdriver = () =>
      Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false, configurable: true });
    await context.addInitScript(hideWebdriver).catch(() => null);
    await page.addInitScript(hideWebdriver).catch(() => null);
  }

  // QA prototype: seed localStorage (JSON object) before the app boots, so a
  // fresh test gateway's first-run guide does not cover the page under test.
  const seed = process.env.JEVONLY_INIT_LOCALSTORAGE;
  if (seed) {
    const entries = JSON.parse(seed);
    await context.addInitScript((kv) => {
      for (const [k, v] of Object.entries(kv)) {
        if (window.localStorage.getItem(k) === null) window.localStorage.setItem(k, v);
      }
    }, entries);
  }

  return { browser, page, context, headless, profileDir, channel, viewport };
}

module.exports = { launchBrowser, loadPlaywright, playwrightCandidates };
