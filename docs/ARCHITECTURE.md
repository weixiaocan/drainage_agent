# Architecture v2

## Layers

- `analysis/`: deterministic drainage-domain analysis. `io.py` is the only data-loading module; other modules accept data frames and parameters, then return structured results.
- `agent/`: Pydantic AI registration, dependencies, prompts, CLI, and thin tool wrappers.
- `web/`: FastAPI upload, chat, result listing, and download endpoints.

## Tool Contract

All tools return `ToolResult` with `ToolStatus = ok | needs_input | error`.

- `ok`: calculation completed and artifacts/data are available.
- `needs_input`: only used when rain-event `event_ids` must be selected by the user.
- `error`: deterministic failure or engineering/configuration problem.

## Public Tools

- `query_stats`
- `check_data`
- `analyze_rainfall`
- `analyze_event_response`
- `analyze_patterns`
- `analyze_rdii`
- `assess_risk`
- `generate_report`
- `list_results`
- `run_python`
- `record_note`

## State And Freshness

Standard artifacts are written under `outputs/`. `manifest.json` records input fingerprints, parameters, generated time, and artifacts. `list_results` compares the manifest fingerprint with current input data to expose fresh/stale state.

## LLM Boundary

LLM usage is limited to agent orchestration, ad-hoc code generation, classification/explanation text, and report prose. Numeric calculation, filtering, thresholds, and tabular analysis are deterministic code paths.

