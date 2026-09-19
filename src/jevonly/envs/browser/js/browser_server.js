#!/usr/bin/env node
'use strict';

const { BrowserActions } = require('./actions');
const { launchBrowser } = require('./playwright');
const { runProtocol } = require('./protocol');

/** Launch the browser child and serve JSON-lines commands. */
async function main() {
  const environment = await launchBrowser();
  const actions = new BrowserActions(environment);
  await actions.init();
  await runProtocol(actions);
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`${String(error && error.stack ? error.stack : error)}\n`);
    process.exitCode = 1;
  });
}

module.exports = { main };
