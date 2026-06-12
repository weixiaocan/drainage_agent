# Drainage Agent PRD v2

## Goal

Provide a conversational drainage-monitoring analysis agent that can inspect uploaded monitoring data, run deterministic domain analysis, and generate reusable artifacts and reports.

## Core Capabilities

- Data access and normalization through `analysis.io`.
- Flow, level, and velocity aggregation through `query_stats`.
- Data quality inspection through `check_data`.
- Rainfall daily/event analysis through `analyze_rainfall`.
- Rain-event response, RDII, pattern, and risk analysis through dedicated tools.
- Report generation from computed results only.
- Result reuse and freshness tracking through `outputs/manifest.json`.

## Interaction Rules

- Ask the user only for rain-event `event_ids` when a requested rainy-weather analysis needs selected events.
- Do not ask for deterministic prerequisites; tools compute or refresh them internally.
- Reuse fresh results when parameters and input fingerprints match.
- Explain abnormal data quality using concrete numbers such as collection rate, effective days, event count, and removed-day ratio.

## Acceptance Criteria

- Public tools are the v2 tool list in `docs/ARCHITECTURE.md`.
- Tool statuses are limited to `ok`, `needs_input`, and `error`.
- CLI and Web keep their conversation loop and message history behavior.
- `run_python` exposes `load_flow`, `load_rain`, and `load_sites`.
- Tests cover core tool success paths, `needs_input`, freshness metadata, and report generation.
