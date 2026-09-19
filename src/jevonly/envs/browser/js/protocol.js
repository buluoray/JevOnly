'use strict';

const readline = require('readline');
const { snapshot } = require('./snapshot');

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
      return { ok: true, snapshot: await snapshot(actions.currentPage(), command) };
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

module.exports = { dispatchCommand, runProtocol };
