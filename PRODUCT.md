# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Security analysts (incident response) who maintain events during or after an incident. Viewers read along; admins manage accounts and export post-mortem reports. Roles in the app: `viewer`, `analyst`, `admin`.

## Product Purpose

Chronicle organises security knowledge. **Events** (time, actor, system, action, status, evidence) belong to **Topics** (incident, vulnerability, project, special). From them it builds a timeline, an entity graph and a detail view. Success: an analyst can reconstruct and document what happened, and hand over a self-contained report.

## Positioning

The topic files (YAML, kept in Git/OneDrive) are the source of truth; the UI, API, static pages and HTML reports are views on them. Self-hosted. A report is a single offline HTML file with timeline, graph, details and embedded evidence, needing no server or login.

## Operating Context

Server-rendered web UI (Python backend, Jinja templates, Tailwind CSS built with npm) over a SQLite index. Run via Docker or locally, bound to 127.0.0.1 by default, optionally behind a Caddy TLS proxy. Also used through CLI (`chronicle check`, `apply`, `build`), API and static HTML output.

## Capabilities and Constraints

- Sign-in, roles, user and audit admin, optional read lock with shared password.
- Create, edit and delete topics and events; link events across topics; upload and preview evidence (PNG, JPEG, GIF, WebP, PDF, text; no SVG/HTML).
- Search (`/` shortcut), help page with shortcuts, dashboard embedded in the topic page.
- Phase colour semantics are part of the data model: red = Identification/Exploit, yellow = Analysis, orange = Containment, green = Mitigation/Recovery, blue = Lessons Learned.
- UI language is German (all existing copy).
- Reports and static pages must work offline and reproducibly (stylesheet and attachments embedded).
- Not decided: explicit accessibility standard, keyboard-first requirement, and a ban on external requests were not confirmed by the owner.

## Evidence on Hand

Fictional demo data in `data/`, dashboard screenshot in `docs/screenshots/dashboard.png`, logo at `chronicle/static/icon.png`, ADRs in `docs/adr/`. No real customers, testimonials or benchmarks exist; do not invent any.

## Product Principles

- Files are the truth; the interface is a view and must never obscure what is on disk.
- Built for analysts under pressure: dense, scannable, precise about time, actor and system.
- Confidential by default: reports and static pages are snapshots of sensitive data and say so.
- Self-contained and offline-capable output.
