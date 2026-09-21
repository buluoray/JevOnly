'use strict';

const readline = require('readline');
const { snapshot } = require('./snapshot');

const NAVIGATION_RACE = /Execution context was destroyed|Cannot find context|Target closed|navigation/i;

/**
 * A snapshot taken while the page is mid-navigation (a redirect landing right as the observation starts:
 * en.wikipedia.org -> Main_Page did this on the very first look) dies with "Execution context was
 * destroyed". That is not an observation of anything; wait for the new document and look again, twice,
 * before letting the error through.
 * @param {import("playwright").Page} page active page
 * @param {object} command wire command
 * @returns {Promise<object>} snapshot
 */
async function snapshotSettled(page, command) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await snapshot(page, command);
    } catch (error) {
      if (attempt >= 2 || !NAVIGATION_RACE.test(String(error.message || error))) throw error;
      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => null);
      await page.waitForTimeout(250);
    }
  }
}

/**
 * Dispatch one parsed wire command.
 * @param {object} command parsed command
 * @param {import("./actions").BrowserActions} actions browser action controller
 * @returns {Promise<object>} response object
 */
async function dispatchCommand(command, actions) {
  switch (command.cmd) {
    case 'goto':
      return actions.goto(command);
    case 'snapshot':
      return { ok: true, snapshot: await snapshotSettled(actions.currentPage(), command) };
    case 'act':
      return actions.act(command);
    case 'inject_popup':
      return actions.injectPopup();
    case 'truncate_fill':
      return actions.truncateFill(command);
    case 'screenshot':
      return actions.screenshot(command);
    case 'keyboard':
      return actions.keyboard(command);
    case 'back':
      return actions.back();
    case 'noop':
      return { ok: true };
    case 'inflight':
      return actions.inflight();
    case 'wait_inflight':
      return actions.waitInflight(command);
    case 'probe':
      return actions.probe();
    case 'quit':
      return { ok: true, quit: true };
    default:
      return { ok: false, error: 'unknown cmd' };
  }
}

/**
 * Run the one-request/one-response JSON-lines protocol.
 * @param {import("./actions").BrowserActions} actions browser action controller
 * @param {NodeJS.ReadableStream} input command stream
 * @param {NodeJS.WritableStream} output response stream
 * @param {(code:number) => void} exit process exit hook
 * @returns {Promise<void>}
 */
async function runProtocol(actions, input = process.stdin, output = process.stdout, exit = process.exit) {
  const reader = readline.createInterface({ input });
  const send = (value) => output.write(`${JSON.stringify(value)}\n`);

  for await (const line of reader) {
    let command;
    try {
      command = JSON.parse(line);
    } catch (_error) {
      send({ ok: false, error: 'bad json' });
      continue;
    }

    try {
      const response = await dispatchCommand(command, actions);
      if (response.quit) {
        send({ ok: true });
        await actions.close();
        exit(0);
        return;
      }
      send(response);
    } catch (error) {
      send({ ok: false, error: String(error).slice(0, 300) });
    }
  }
  await actions.close();
}

module.exports = { snapshotSettled, dispatchCommand, runProtocol };
