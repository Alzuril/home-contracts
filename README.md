# Домашні контракти

Two-person gamified household chore tracker. PyScript frontend (GitHub Pages),
Supabase backend. See `docs/superpowers/plans/2026-09-08-home-contracts-baseline.md`
for the implementation plan.

## Local dev prerequisites

- Docker Desktop running (for `supabase start`)
- [Supabase CLI](https://supabase.com/docs/guides/cli) installed
- Python 3.11+ with `pytest`, `pytest-asyncio`, `responses` installed (`pip install -r requirements-dev.txt`)
- Any static file server for the frontend, e.g. `python -m http.server 8000`

## Status

Bootstrapped without Docker/Supabase CLI/Node/gh available on this machine —
see the plan's task notes for which verification steps are still pending a
machine with those tools installed.
