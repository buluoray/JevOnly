# JevOnly documentation

| Document                                      | Use it for                                                                                   |
| --------------------------------------------- | -------------------------------------------------------------------------------------------- |
| [How it works](how-it-works.md)               | The closed-vote loop, exact Jev questions, thresholds, recovery, copy, and keyboard behavior |
| [Architecture](architecture.md)               | Components, process boundaries, JSON-lines transport, viewer thread, and SSE data flow       |
| [Event reference](events.md)                  | Every event emitted by `run_task`, with payload fields and viewer transport notes            |
| [Browser child protocol](browser-protocol.md) | Every Python-to-Node wire command and response                                               |
| [FAQ](faq.md)                                 | Cost, undo, `none`, copy, fixtures, CAPTCHAs, typing, and troubleshooting concepts           |

## Start here

1. Follow the [root quickstart](../README.md#run-it-in-60-seconds).
2. Read [How it works](how-it-works.md) before changing the decision loop.
3. Read [Architecture](architecture.md) before changing process or transport boundaries.
4. Treat [Events](events.md) and [Browser child protocol](browser-protocol.md) as compatibility contracts for integrations.
