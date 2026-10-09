# Model latency and memory-context metrics

## What is available in Codex and Google Antigravity

The robot MCP servers do not make model API requests. A tool runtime is not
model latency, and a JSON string length is not a token count. This implementation
keeps three distinct measurements in the existing per-run JSONL:

| Measurement | Collection | Meaning |
| --- | --- | --- |
| `cognitive_stage_start/end` | Two MCP tools on CognitiveMemory | Server-measured Stage 0/3 (or 1-5) wall time, including intervening tools, host waits and instrumentation |
| `tool_start/end.memory_metrics` | Automatic for all five memory tools | STM/LTM argument and returned payload characters and UTF-8 bytes; tokens explicitly unavailable |
| `model_request_start/end` | Host-side wrapper or measured JSON import | Actual request/stream duration and provider-reported token usage, when exported by the host |

No subscription change or direct API key is needed for the first two. Restart
the five MCP servers once to load the updated code; CognitiveMemory now exposes
two additional tools. No extra MCP server or client configuration is required.
No client telemetry settings are silently changed by this repository.

Official Codex documentation describes opt-in OpenTelemetry events including
API durations and token counts on completed responses. These require host-side
export configuration; they are not automatically passed to MCP servers.
An HTTP request duration or SSE event-processing duration is not necessarily
full streamed-response latency. Map only an interval with the documented
start/end semantics into `latency_seconds`.
See [Codex observability](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry).
The [Antigravity MCP documentation](https://antigravity.google/docs/mcp)
describes MCP connectivity but does not establish a provider-usage feed to MCP
tools. This project does not assume such a feed exists. If your client version
does not export usage, exact token/API latency remains unavailable; the stage
and memory-size measurements still work. The JSON importer is an integration
boundary, not an automatic Codex/Antigravity telemetry collector.

## Instrumented experiment workflow

Use a distinct run for each model, scenario and repetition. Start from the same
initial memory snapshot and robot pose, and record actual model selection,
client version, reasoning settings, variant and task outcome. Do not compare
short failed runs with successful task completion as evidence of efficiency.

```bash
python3 modules/experiment_log.py --run-id codex_trial_01 --metadata '{"client":"codex","provider":"openai","model":"YOUR_EXACT_MODEL_ID","scenario":"direct_target","variant":"full","repetition":1}'
```

For Antigravity, use `client: antigravity`, `provider: google`, the actual Gemini
model identifier from the host, and a different run-id. Never infer model IDs
from a tool name. Unknown labels must be recorded as unknown, not guessed.

Include this instruction in the benchmark task for BOTH clients:

> In this instrumented experiment, immediately before each Stage 0 and Stage 3,
> call start_cognitive_stage with the stage number and the operator-supplied
> provider/model. Keep the returned stage_id. Immediately after that stage,
> call finish_cognitive_stage with that ID and outcome completed, failed, or
> cancelled. These are observed stage intervals, not API timings. Do not invent
> or estimate token counts. Do not change the navigation/safety protocol.

Call the same markers around Stage 5 if memory-update stage timing is desired.
The baseline has no Stage 0: do not introduce it for timing. Both variants have
the same two optional telemetry tools; the baseline navigation-tool policy is
otherwise unchanged. Markers add host round trips and context overhead, so use
the same instrumentation policy for every compared model.

Stage labels are caller-supplied, not automatically verified against reasoning.
An unfinished stage remains visible. Duplicate finishes are rejected. A stage
finishes in its original run even if the marker changed. Rebooted-clock timings
are null rather than fabricated. Prefer non-overlapping stages and one model per
run. The tools measure from marker entry; they cannot time planning that happened
before the start marker was called.

## Exact API telemetry integration

If you control a Python API host, use the wrapper around ONE request/attempt:

```python
from model_metrics import ModelRequest

with ModelRequest(provider=provider, model=exact_model_id,
                  stage=3, purpose="planning", run_id=experiment_run_id) as metric:
    response = make_one_api_request()
    metric.set_usage(normalized_provider_usage, provider_request_id=response_id)
```

The context manager also works inside async code around an `await`. It excludes
start/end log writes from the timed interval. For streaming keep the context
open until completion, call `first_token()` on the first actual content delta,
and supply usage from the final provider event. Without that call TTFT is null.
SDK-internal retries are included in the measured call unless disabled; only
explicitly wrapped attempts can be reported separately. Do not wrap an entire
agent loop containing robot tool execution as one API request.

For existing client exports, create a JSON object using these fields:

```json
{
  "run_id": "codex_trial_01",
  "request_id": "unique-host-request-and-attempt-id",
  "provider": "openai",
  "model": "YOUR_EXACT_MODEL_ID",
  "stage": 3,
  "purpose": "planning",
  "latency_seconds": 2.5,
  "status": "completed",
  "usage": {"input_tokens": 1000, "output_tokens": 100},
  "attempt": 1
}
```

Numbers above are an example, NOT measurements. Replace them with actual host
records before import. Use null for an unknown/mixed stage. A response spanning
Stage 0 and Stage 3 must not be counted in full under both. Allowed purposes are
`planning`, `stm_update`, `ltm_update`, `other`. An update-purpose request measures
the full request, not just tokens used to generate the memory tool arguments.
`tool_call_ids` optionally links exact JSONL tool call IDs supplied by the host;
no association is inferred from timing proximity.

```bash
python3 modules/model_metrics.py /path/to/actual_host_record.json
python3 modules/metrics_report.py logs/run_codex_trial_01.jsonl logs/run_gemini_trial_01.jsonl > logs/model_comparison.json
```

Import requires an existing run and rejects duplicate request IDs. Keep one
unique request ID per attempt. Import does not switch the active run.

### Normalized token semantics

- `input_tokens`: total request input, INCLUDING cached input.
- `output_tokens`: total output, INCLUDING reasoning tokens if separately reported.
- `total_tokens`: input plus output; derived only when both are known.
- `cached_input_tokens`: optional subset of input, never added again.
- `reasoning_output_tokens`: optional subset of output, never added again.
- Missing values stay null. A real zero stays zero. No character/4 heuristics.

The host must normalize its provider's accounting before import; SDK field
names are not blindly interchangeable. `usage` accepts only these fields and
represents provider measurements. Unknown image/audio/hidden-context accounting
must not be reconstructed from MCP payload bytes.

Optional `context_tokens` attributes the STM/LTM content actually placed into
that request: `stm_tokens`, `ltm_tokens`, `method` (`provider` or `tokenizer`),
and a required exact `tokenizer` identifier/version for tokenizer counts.
Tokenizer attribution is separate from provider-billed totals and can differ
because of framing. Do not supply this attribution if the host cannot observe
the actual prompt. Conversation replay can charge the same memory content
multiple times; an MCP read alone does not establish what was retained in later
requests. Input/output payload sizes measure read/write content, not the entire
persisted memory file or full client context window.

## Report interpretation

The report groups API attempts by run, provider, model, stage and purpose.
It separates completed-response mean/p50/p95 from all attempts and reports
known token sums with missing-usage counts. Failed/cancelled attempts still
consume resources when usage is available. Stage wall times have a separate
section and are never substituted for API latency. STM/LTM payload totals are
per run and operation; run metadata identifies the configured model. Repeated
reads accumulate. Compare both token coverage and equivalent task outcomes
before drawing efficiency conclusions. No model ranking is manufactured when
API telemetry is absent. Logs with duplicate events or malformed JSON are
rejected rather than silently undercounted.

## Offline verification

```bash
python3 -m unittest discover -s tests -p test_model_metrics.py -v
python3 -m unittest discover -s tests -p test_experiment_log.py -v
```
