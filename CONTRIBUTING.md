# Contributing

This project is intentionally lightweight: no third-party runtime dependencies,
static dashboard output, and GitHub Actions as the default scheduler. When you
change behavior, please keep those constraints in mind.

## Setup

```bash
cd imaging-monitor
cp .env.example .env
python3 tests/run_all.py
```

You can work fully offline with fixtures:

```bash
python3 -m imaging_monitor.cli run --fixture-dir tests/fixtures --dry-run --skip-push --no-ai
```

## Development Guidelines

- Keep runtime dependencies at zero unless there is a strong reason.
- Prefer extending existing modules over introducing a new abstraction layer.
- Preserve the current source-of-truth flow:
  `fetchers -> rules -> ai -> store -> report -> feishu/web`.
- Every pushed event must keep an evidence link.
- Avoid changes that would make GitHub Actions require extra services.

## Testing

Run the full suite before proposing changes:

```bash
python3 tests/run_all.py
python3 -X pycache_prefix=/private/tmp/imaging-monitor-pycache -m compileall imaging_monitor tests
```

If you touch AI behavior, also run at least one fixture-based dry run:

```bash
python3 -m imaging_monitor.cli run --fixture-dir tests/fixtures --max-ai-items 1 --dry-run --skip-push
```

## Data Files

The `data/` directory is intentionally versioned.

- `data/events.json`, `data/report.json`, `data/quality_report.json`, and `data/store.json`
  are both runtime outputs and demo artifacts for the dashboard.
- Do not remove them just because they are generated.
- If you change the output schema, update README and dashboard rendering in the same change.
