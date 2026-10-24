# Usage Report — Ghost Data Still Exists

All figures come from recorded ledgers, not estimates:

- the per-step usage ledgers printed by `code/main.py` and the smoke test in the GitHub Actions job
  logs of workflow "Deletion audit run" (runs #2, #3 and the cached replays); and
- the 409 model-output cache entries, each of which stores the usage the API reported for the call
  that produced it. Their totals (409 calls, 409,031 input / 37,364 output tokens, $2.3834) equal the
  sum of run #3's live smoke, sample and target ledgers exactly; every entry was created in run #3
  (2026-10-24 16:31:43Z–16:47:11Z).

Costs use the price table in `code/agent/llm.py` for `claude-opus-5-5`: $4 per million input tokens and
$20 per million output tokens. No prompt caching was used (cache read/write tokens are 0 in every
record). No other model or provider was called; automatic model fallback is disabled.

## Models

| Provider | Model | Purpose | Settings |
|---|---|---|---|
| Anthropic | `claude-opus-5-5` | request-intent classification | JSON-schema output, effort low, max 1,024 output tokens, prompt `intent-v2` |
| Anthropic | `claude-opus-5-5` | screenshot fact transcription (11 PNG files) | JSON-schema output, effort medium, max 16,000 output tokens, prompt `vision-v1` |

## Logical model uses vs actual API calls

A *logical use* is one point where the pipeline needs a model output (one intent per request, one
extraction per screenshot). An *actual API call* is a paid request to the Anthropic API. Outputs are
cached by model, purpose, prompt version and input digest, so a repeated request text or a screenshot
needed by several runs is paid for once.

The 518-request target audit has **529 logical uses** (518 intents + 11 screenshot extractions). They
are served by **386 distinct model outputs** (375 distinct request texts + 11 screenshots). The 529
logical uses are **not** 529 paid API calls.

## Actual API calls (run #3, id 40111949889, commit `a6c63f8`)

Every model output used by the submission was produced in run #3:

| Run #3 step | Actual API calls | Input tokens | Output tokens | Actual cost |
|---|---:|---:|---:|---:|
| Smoke test (1 sample intent, 1 screenshot) | 2 | 5,022 | 1,035 | $0.0408 |
| Sample audit (32 samples) | 41 | 67,461 | 14,187 | $0.5536 |
| Target audit (518 requests) | 366 | 336,548 | 22,142 | $1.7890 |
| **Run #3 total** | **409** | **409,031** | **37,364** | **$2.3834** |

## What the target output depends on (deduplicated original production cost)

The target audit used 386 distinct outputs. 366 were produced by its own step; 20 had been produced
earlier in run #3 and were reused from the cache. Each is counted once, at the usage recorded when it
was produced:

| Distinct outputs used by the target audit | Count | Input tokens | Output tokens | Production cost |
|---|---:|---:|---:|---:|
| Produced in the target step | 366 | 336,548 | 22,142 | $1.7890 |
| Reused from the sample step (9 request texts shared with samples, 10 screenshots) | 19 | 47,430 | 12,687 | $0.4435 |
| Reused from the smoke step (1 screenshot) | 1 | 4,115 | 970 | $0.0359 |
| **Target dependencies, deduplicated** | **386** | **388,093** | **35,799** | **$2.2684** |
| of which intent (375 distinct texts) | 375 | 344,881 | 22,696 | $1.8334 |
| of which screenshot extraction | 11 | 43,212 | 13,103 | $0.4349 |

Per target request (÷ 518), deduplicated: 0.75 distinct outputs, 749.2 input + 69.1 output = 818.3
tokens, **$0.00438**.

Sample-only work (not needed by the target output): 23 intents for sample texts that do not occur in
the target set, 20,938 input / 1,565 output tokens, $0.1151. The 32 sample intents and the 11
screenshots also needed by the samples are included above where they are shared.

Check: target dependencies $2.2684 + sample-only $0.1151 = $2.3835 vs run #3 total $2.3834 (rounding
of per-group sums).

## Final handoff runs (cache replay)

Runs #4–#10 and the final handoff run replay these outputs from the Actions cache: **0 API calls,
$0.00**. Their ledgers record the 529 logical uses as `historical_cached` with the usage of the
original calls; that figure is not new spend.

## Hypothetical cost without reuse (not actual spend)

If each of the 529 logical uses of the target audit were a separate API call with the usage recorded
for its output, the cost would be **$2.9786** (521,370 input + 44,655 output = 566,025 tokens; per
request 1,092.7 tokens, $0.00575). Of that, 163 uses were cache hits inside run #3's target step,
recorded in its ledger as `historical_cached` $1.1895 including duplicates; deduplicated, those hits
are the 20 reused outputs costing $0.4794 above. These are hypothetical comparison figures; nothing
was paid for them.

## All actual spend

| Run | What ran live | Actual API calls | Actual cost |
|---|---|---:|---:|
| #1 (id 40110065932) | key rejected by the API | 0 | $0.0000 |
| #2 (id 40110467404) | smoke + samples + targets with intent prompt v1 (superseded; outputs not used) | 409 | $2.1549 |
| #3 (id 40111949889) | smoke + samples + targets with intent prompt v2 (all outputs used by the submission) | 409 | $2.3834 |
| #4–#10 and final handoff run | none (cache replays) | 0 | $0.0000 |
| **Total** | | **818** | **$4.5383** |

Run #2's figure is the sum of its smoke ($0.0408), sample ($0.5372) and target ($1.5769) ledgers. Its
intent outputs used prompt v1 and are not part of the cache key the submission uses.

## How usage is recorded

`code/agent/llm.py` records every call (model, purpose, live or cache source, input/output and
prompt-cache tokens) in a ledger before the response is validated, so refused, truncated or invalid
responses are still counted. Cost is `None` (reported as "unknown", never zero) for any model without
a price entry. `main.py` writes `code/eval/usage_<run>.json` and prints per-purpose rows plus
`live_run` (actual calls in this run) and `historical_cached` (reused outputs at their original
usage, counted once per logical use) totals. No credentials appear in any report.
