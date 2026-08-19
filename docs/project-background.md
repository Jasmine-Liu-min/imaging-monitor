# Project Background

## Overview

`imaging-monitor` is a lightweight AI-native competitor intelligence system for the imaging category.

It monitors domestic and global sources across:

- action cameras
- drones
- gimbals
- mirrorless cameras
- companion apps
- creator/community signals

The goal is not broad web search for its own sake. The goal is a stable internal pipeline that produces readable Chinese monitoring output with evidence links, minimal noise, and low operating cost.

## Problem Statement

Before this project, competitor intelligence had four recurring issues:

- Information was slow to reach decision makers
- Signals were scattered across official sites, media, app stores, and communities
- General tech sources carried too much non-imaging noise
- The same story was repeatedly re-surfaced without new information

For a fast-moving category, that combination makes manual monitoring expensive and unreliable.

## Design Goals

This project was designed around a few constraints:

- Low cost
- Low operational complexity
- Deterministic source collection
- AI used for extraction and analysis, not for hallucinated discovery
- Evidence required for pushed conclusions
- Static outputs that are easy to host and demo

It intentionally does not depend on Manus or any existing internal monitoring project.

## Technical Choices

### 1. Source-first, not AI-first discovery

The system starts from configured sources in:

- `config/sources.yaml`
- `config/keywords.yaml`

This keeps the collection layer auditable and stable. AI only operates on candidates that have already been collected from known sources.

### 2. Lightweight runtime

The runtime uses the Python standard library only.

That choice keeps:

- GitHub Actions setup simple
- local setup friction low
- demo environments predictable

### 3. AI as structured extraction + analysis

The AI layer is split conceptually into:

- extractor
- analyst

Extractor turns source candidates into structured events.
Analyst adjusts importance, confidence, threat, and push decision.

If AI fails, the pipeline degrades to rule-based fallback instead of stopping the entire run.

### 4. Cross-run memory

`data/store.json` is used as persistent memory across runs.

This supports:

- first-seen tracking
- new vs old event classification
- change-aware push behavior
- simple trend summaries for the dashboard

### 5. Static dashboard + Feishu push

Outputs are written as JSON, then reused by:

- `web/index.html`
- `imaging_monitor/feishu.py`

This avoids building separate backend and frontend data models.

## End-to-End Flow

```text
Configured sources
  -> fetchers.py
  -> rules.py filtering and dedup
  -> ai.py extraction / analysis
  -> store.py cross-run memory
  -> report.py report + battlecards + quality report
  -> feishu.py push card
  -> web/index.html dashboard
```

## Why Keep Generated Data In Git

Normally generated artifacts are ignored. Here they are intentionally kept.

Reasons:

- the dashboard reads directly from `data/`
- the repository doubles as a demo artifact
- `store.json` is part of the functional behavior, not just a cache

This is a product decision, not an accident.

## Output Surfaces

### Feishu

The Feishu card is optimized for daily reading:

- new events first
- grouped sections
- evidence links
- brief strategic interpretation

### Dashboard

The dashboard supports:

- overview
- key events
- battlecards
- category tabs
- region/language/brand/threat filters
- basic trend visualization

## Results So Far

The current version already has:

- provider-agnostic AI access
- domestic/global split
- Chinese/English source support
- evidence-aware push gating
- cross-run dedup
- static dashboard deployment path
- fixture-based local testing

It is now much closer to a real internal product than a throwaway prototype.

## Tradeoffs

This project intentionally accepts some limitations:

- source coverage is curated, not universal
- HTML parsing is lightweight, not browser-driven
- AI output quality still depends on provider stability
- some rich media / JS-heavy official pages are replaced by alternate feeds or aggregations

Those tradeoffs are part of keeping the system low-cost and easy to operate.

## Future Directions

Natural next steps would be:

- more source-specific parsers for official pages
- stronger quality metrics across runs
- optional richer packaging for public/open-source use
- selective deep-analysis mode for especially important events
