# Frontend Performance Skill

Evidence-driven frontend performance diagnosis for AI coding agents.

[English](README.md) | [简体中文](README_ZH.md)

`frontend-performance` is a portable Agent Skill for Codex, Claude Code, Cursor, pi, OpenClaw, and Hermes Agent. It investigates slow pages, poor Core Web Vitals, runtime jank, large bundles, slow builds, static assets, and network delivery.

It works from the user journey and the available evidence. A Lighthouse score is not production truth, a large package is not automatically a bottleneck, and a long trace event is not a root cause by itself.

If you want your coding agent to work from traces, requests, build output, and field data instead of returning a generic optimization checklist, this skill is a good fit. It helps identify what is worth fixing first and how to verify the result.

## What it covers

- Baselines using field data, RUM, CrUX, Lighthouse, and Chrome DevTools.
- Chrome Performance traces, flame charts, sanitized HAR files, and Network waterfalls.
- LCP, INP, CLS, long tasks, rendering work, unnecessary updates, and memory growth.
- Bundles, import boundaries, tree-shaking, duplicate dependencies, repeated implementations, and build time.
- Production output, source maps, content hashing, compression, caching, and deployment checks.
- Delivery guidance for Vite, webpack, Next.js, and Nuxt.
- Images, fonts, SVG, CSS/JavaScript, audio, video, PDFs, and format-specific compression.
- DNS, TCP/TLS, TTFB, redirects, HTTPS, cross-origin requests, CDNs, and third parties.
- Reviews based on the coverage areas of the Front-End Performance Checklist.
- Before/after validation, performance budgets, and regression checks.

## How it works

1. Define the user journey, environment, cache state, and success condition.
2. Collect the smallest useful set of evidence.
3. Locate the limiting stage: server, network, JavaScript, rendering, interaction, memory, or build.
4. Separate observations from hypotheses and rank findings by impact, confidence, cost, and risk.
5. Test a focused change under the same conditions, then confirm the result with field data when available.

[SKILL.md](SKILL.md) contains the main workflow and routes the active agent to focused guides in [`references/`](references/) only when they are relevant.

## Compatibility

The package follows the [Agent Skills specification](https://agentskills.io/specification): a `SKILL.md` entry point with optional `references/`, `scripts/`, and host metadata. The core instructions do not depend on a Codex-only tool or invocation syntax.

| Host | Recommended user-level directory |
|---|---|
| [Codex](https://developers.openai.com/codex/skills) | `~/.agents/skills/frontend-performance` |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills/frontend-performance` |
| [Cursor](https://cursor.com/docs/skills) | `~/.agents/skills/frontend-performance` or `~/.cursor/skills/frontend-performance` |
| [pi](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md) | `~/.agents/skills/frontend-performance` or `~/.pi/agent/skills/frontend-performance` |
| [OpenClaw](https://docs.openclaw.ai/skills) | `~/.agents/skills/frontend-performance` or `~/.openclaw/skills/frontend-performance` |
| [Hermes Agent](https://hermes-agent.nousresearch.com/docs/guides/work-with-skills) | `~/.agents/skills/frontend-performance` or `~/.hermes/skills/frontend-performance` |

`agents/openai.yaml` only adds Codex UI metadata. Other hosts can ignore it. Loading the skill is portable; running its helpers still requires the host to allow local file access and shell commands.
