# Security Policy

## Supported versions

JevOnly is pre-1.0 software. Security fixes are provided for the latest `0.x` release only.

| Version       | Supported |
| ------------- | --------- |
| Latest `0.x`  | Yes       |
| Earlier `0.x` | No        |

## Reporting a vulnerability

Use GitHub private vulnerability reporting for security issues. Do not open a public issue for an undisclosed vulnerability, and do not include API keys, session data, personal data, or private-site content in a report.

Include the affected version, impact, reproducible steps using synthetic data, and any suggested mitigation. A maintainer will acknowledge the report within 3 business days, provide an initial assessment within 7 business days, and send progress updates at least every 14 days until the report is resolved or declined. Disclosure timing will be coordinated with the reporter.

## Security scope

Security reports are especially useful for:

- bypasses of the viewer's loopback-only binding;
- exposure of `TYPESAFE_API_KEY` through logs, events, process output, fixtures, or browser-visible content;
- failures of the irreversible-action rule: an action Jev judged irreversible that is retried, forced through as least-bad, or performed while the previous action's requests are still in flight.

## Threat model and what is out of scope

JevOnly is a tool the operator runs on their own machine. The viewer binds to 127.0.0.1 only and has no
authentication of its own; anyone who can reach that port is the operator. Within that model:

- There is no allow- or deny-list of destinations. The operator chooses the start page; everything the
  loop observes from then on -- including pages reached by redirects or links -- is sent to TypeSafe as
  part of each judgment. Point it only at pages whose content you are willing to send there.
- Actions Jev classifies as irreversible pass a gate before they run: `refuse` (the CLI default), `ask`
  (the viewer default: the run pauses for the operator) or `allow`. The classification is Jev's judgment,
  so an action it scores below the risk line runs like any other; lower the `risk` line to widen the net.

Reports about upstream browser or Playwright vulnerabilities should identify a JevOnly-specific impact. Reports that require deliberately disabling documented safety controls may be closed as out of scope.

## Operational guidance

Provide `TYPESAFE_API_KEY` only through the process environment. Never place it in goals, facts, event logs, fixtures, command-line arguments, or committed configuration. Run the viewer on its default loopback address, review the proposed start URL, and inspect the timeline before allowing consequential actions.
