# Toadstone documentation

> For the complete documentation index, see [llms.txt](https://spcaeo.github.io/toadstone/llms.txt). Every page here is also served as Markdown at the same address with `.md` instead of `.html`.

Toadstone is a free, self-hosted SEO crawler, source-available under the Elastic License 2.0 (not OSI open source). The documentation below is the same text that ships in the repository at [docs/ on GitHub](https://github.com/spcaeo/toadstone/tree/main/docs).

## Pages

- [The check reference](https://spcaeo.github.io/toadstone/docs/checks.html) ([Markdown](https://spcaeo.github.io/toadstone/docs/checks.md)): Every check Toadstone can report (222 in total), with its slug, its severity and what to do about it.
- [Toadstone MCP server](https://spcaeo.github.io/toadstone/docs/mcp.html) ([Markdown](https://spcaeo.github.io/toadstone/docs/mcp.md)): Ten tools that let a coding agent read a crawl and find what is wrong with a site, read-only by default, with setup for Claude Code and Cursor, its guardrails and its transports.
- [Agent handoff: fix the SEO issues from a Toadstone crawl](https://spcaeo.github.io/toadstone/docs/agent-handoff.html) ([Markdown](https://spcaeo.github.io/toadstone/docs/agent-handoff.md)): A paste-in prompt that hands a crawl to a coding agent: read the issues over the HTTP API, fix them in the site code, re-crawl and compare.
- [The agent layer, and how to give it a model](https://spcaeo.github.io/toadstone/docs/agents.html) ([Markdown](https://spcaeo.github.io/toadstone/docs/agents.md)): How Toadstone can ask a language model to judge pages, why it is off by default, the three agentMode settings, and the Docker bridge for cli mode.
- [Connecting Google Search Console and Analytics](https://spcaeo.github.io/toadstone/docs/google.html) ([Markdown](https://spcaeo.github.io/toadstone/docs/google.md)): The one-time OAuth client setup, the four read-only scopes, where the credential is stored and how errors are explained.
- [Writing a plugin](https://spcaeo.github.io/toadstone/docs/plugins.html) ([Markdown](https://spcaeo.github.io/toadstone/docs/plugins.md)): How to add a tab to Toadstone with one JavaScript file and no build step, with the registration API, lifecycle hooks and styling classes.
- [The name](https://spcaeo.github.io/toadstone/docs/about-the-name.html) ([Markdown](https://spcaeo.github.io/toadstone/docs/about-the-name.md)): Why the tool is called Toadstone, what a toadstone was, and where the project comes from: a fork of LibreCrawl.

## Elsewhere

- [Live demo](https://spcaeo.github.io/toadstone/): the real interface, frozen over a real crawl, no sign-up.
- [Guided tour](https://spcaeo.github.io/toadstone/watch.html): a short video tour.
- [Source code and README](https://github.com/spcaeo/toadstone): Toadstone on GitHub.
- [llms.txt](https://spcaeo.github.io/toadstone/llms.txt): the machine-readable index of these docs.

Last updated: 2026-10-20.
