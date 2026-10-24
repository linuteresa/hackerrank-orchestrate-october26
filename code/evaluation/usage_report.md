# Usage Report — Ghost Data Still Exists

## Status of the final run

The full-dataset run below audited all 518 requests and validated every row, but the export gate
refused to write `output.csv` because 115 rows are blocked (see `evaluation_report.md`). The usage
figures are therefore for the latest complete full-dataset run, not for a submitted `output.csv`.
Reported from the job logs of GitHub Actions workflow "Deletion audit run" runs #3 and #4.

- Run #3 (id 40111949889, commit `a6c63f8`): every model output was produced live in that run (the
  sample step produced the 32 sample intents and 11 screenshot extractions; the target step reused
  them and produced the rest).
- Run #4 (id 40113277715, commit `72832f6`): the same 518 requests replayed entirely from the
  model-output cache: **0 live calls, $0.00**, identical results.

## Models

| Provider | Model | Purpose | Settings |
|---|---|---|---|
| Anthropic | `claude-opus-5-5` | request-intent classification | JSON-schema output, effort low, max 1,024 output tokens, prompt `intent-v2` |
| Anthropic | `claude-opus-5-5` | screenshot fact transcription (11 PNG files) | JSON-schema output, effort medium, max 16,000 output tokens, prompt `vision-v1` |

Automatic model fallback is disabled; a response served by any other model is rejected (and billed
in the ledger). No other model or provider is used. Pricing used for estimates: $4 per million input
tokens, $20 per million output tokens ($0.20 cache read, $5 cache write; no prompt caching was used,
both 0).

## Full-dataset run (518 requests)

| Purpose | Calls | Input tokens | Output tokens | Total tokens | Estimated cost |
|---|---:|---:|---:|---:|---:|
| intent | 518 | 478,158 | 31,552 | 509,710 | $2.5437 |
| vision | 11 | 43,212 | 13,103 | 56,315 | $0.4349 |
| **Total (claude-opus-5-5)** | **529** | **521,370** | **44,655** | **566,025** | **$2.9786** |

| Per request (÷ 518) | Value |
|---|---:|
| Model calls | 1.02 |
| Input tokens | 1,006.5 |
| Output tokens | 86.2 |
| Total tokens | 1,092.7 |
| Estimated cost | $0.00575 |

Intent alone averages 923 input and 61 output tokens per request. The 11 screenshot extractions are
shared across all requests (each screenshot is transcribed once, then reused through the cache).

The 518 requests contain 375 distinct request texts. Intent output is cached by input digest, so a
repeated text is classified once and reused. The table counts every request at the recorded usage of
its cached call, so it states what a full run costs per request with no reuse; the money actually
spent is in the next table (the target step of run #3 made 366 live calls).

## All live spend so far (for transparency)

| Run | What ran live | Live calls | Live cost |
|---|---|---:|---:|
| #1 (id 40110065932) | key rejected by the API | 0 | $0.0000 |
| #2 (id 40110467404) | smoke + samples + targets, intent prompt v1 (superseded) | 409 | $2.1549 |
| #3 (id 40111949889) | smoke + samples + targets, intent prompt v2 | 409 | $2.3834 |
| #4 (id 40113277715) | none (cache replay) | 0 | $0.0000 |
| **Total** | | **818** | **$4.5383** |

Run #2's live cost is the sum of its smoke ($0.0408), sample ($0.5372) and target ($1.5769) steps;
run #3's of $0.0408, $0.5536 and $1.7890. Run #2's intent outputs (prompt v1) are not used by the
current code because the prompt version is part of the cache key.

## How usage is recorded

`code/agent/llm.py` records every call (model, purpose, live or cache source, input/output and
prompt-cache tokens) in a ledger before the response is validated, so refused, truncated or invalid
responses are still counted. Cost is `None` (reported as "unknown", never zero) for any model without
a price entry. `main.py` writes `code/eval/usage_<run>.json` and prints per-purpose rows plus
`live_run` and `historical_cached` totals, so the cost of reused cache entries is shown separately
from money spent in the current run. No credentials appear in any report.
