# agent_desk - Agent Desk tab

A multi-agent research desk modelled on the
[TradingAgents](https://github.com/TauricResearch/TradingAgents) framework:
specialist analysts hand findings to a bull/bear debate, a trader turns the
debate into a proposal, a risk team sizes it, and a portfolio manager signs off
or blocks it.

The difference from TradingAgents is that there is **no LLM and no API key**.
Every claim is derived from the report `core.services.analyze_symbol` already
builds, so the desk quotes the same numbers the Stock Analysis tab shows. Because
that report is cached, opening this tab right after analysing a stock costs no
extra network calls.

## Why this tab exists

Two problems this fixes:

- **Seamless.** The other tabs each answer one question (chart, fundamentals,
  ownership, levels). Nothing tied them together into a single "so what". This
  tab reads all of them at once and produces one action, one size, one stop.
- **Accurate.** A single blended score hides disagreement. Here each desk scores
  and grades its own data coverage, disagreements between desks are surfaced
  explicitly, and missing inputs are listed. Thin data or an open conflict cuts
  the desk confidence and the position size, so a poorly covered symbol cannot
  produce a confident verdict.

## Pipeline

| Stage | Agent(s) | What it produces |
|-------|----------|------------------|
| 1 | Technical, Fundamentals, Positioning, Macro & News analysts | Score, stance, evidence, concerns, and a self-graded data-coverage confidence. |
| 2 | Bull Researcher vs Bear Researcher, then the Research Manager | 1-3 debate rounds (strongest points first) and a debate `edge` from -100 to +100. |
| 3 | Trader | Action (Buy / Accumulate / Hold / Reduce / Avoid) plus entry, trigger, invalidation, and targets taken from `core.services.build_research_levels`. |
| 4 | Aggressive / Balanced / Conservative risk analysts, then the Risk Manager | Position size from the risk budget divided by the stop distance; the manager picks a stance from event risk, ATR, and grounding. |
| 5 | Portfolio Manager | `Approved`, `Approved with conditions`, `Stand aside`, or `Rejected`, with blocking reasons and conditions. |
| 6 | - | Data grounding checks and cross-desk conflict detection, which feed the desk confidence. |

## How the debate is scored

The debate is a weighted vote of desks, not a count of talking points. Three
rules do the work, and each exists because the naive version was wrong:

- **Correlated readings are collapsed into factors.** "Price below its 50-day
  average", "price below its 200-day average", "50-day below 200-day", and
  "MACD below signal" are four readings of one downtrend, not four reasons to
  sell. Within a factor the strongest argument counts in full and each further
  one is discounted (`CORRELATION_DISCOUNT`), so restating an observation can
  never outvote several independent ones.
- **Each desk argues at its assigned weight.** Price data is always complete, so
  the technical desk always reached full confidence and produced the most
  findings, while scraped fundamentals are usually partial. Multiplying findings
  by desk confidence penalised the thin desks twice and quietly turned the whole
  desk into a momentum model. A desk's voice is now normalised to its policy
  weight, scaled down only until it has brought enough evidence to earn all of it
  (`FULL_VOICE_EVIDENCE_MASS`).
- **Neutral findings are shown but not scored.** "Holding is broadly stable",
  "sector context is mixed", and a scheduled earnings date are informative and
  directionally silent. They used to nudge the verdict.

Two breadth measures then discount the result: how many distinct factors carry
the winning side, and how many desks do. Six price-derived readings from one desk
is not six independent confirmations.

Confidence is a data-quality and coherence measure, not investment merit. A desk
with no data scores `0` confidence and abstains instead of voting its default
neutral, a conflict requires one desk bullish and another bearish (a desk with no
view cannot disagree), and the Portfolio Manager will not open new risk below
`MIN_APPROVAL_DESK_CONFIDENCE` however the debate reads.

Sector context is applied where a single threshold would be wrong: a lender's
debt-to-equity and current ratio are read as structural rather than stretched,
valuation is compared against a sector P/E band and says so when the sector is
unknown, and analyst target upside is measured against the habitual sell-side
premium (`SELL_SIDE_TARGET_PREMIUM`) rather than against spot.

Only two desks are new signal work: **Positioning** (analyst consensus, target
dispersion, promoter/FII/DII flows, option-chain PCR and max pain) and **Macro &
News** (event window, sector rotation, catalyst headlines, policy themes). The
Technical and Fundamentals desks reuse the scores from `core.services` on
purpose, so this tab can never contradict the Stock Analysis tab.

## Files

| File | What it is for |
|------|----------------|
| `agents.py` | The whole pipeline: analyst team, debate, trader, risk team, portfolio manager, grounding, and conflict detection. Reads only `core.services`. |
| `views.py` | `analyze` endpoint; resolves the symbol, runs the desk, logs the search. |
| `urls.py` | Route `/api/agent-desk/analyze`. |
| `apps.py` | Django app config. |
| `tests.py` | Tests covering each stage, the scoring rules above, and the API. |
| `templates/agent_desk/tab.html` | The tab markup. Included by `core/base.html`, populated by `public/app.js`. |

## Endpoints

- `GET /api/agent-desk/analyze?symbol=RELIANCE.NS` - run the desk
- `GET /api/agent-desk/analyze?symbol=RELIANCE.NS&rounds=3` - deeper debate
  (`rounds` is clamped to 1-3; anything invalid falls back to 2)

Error behaviour matches `/api/analyze`: `400` with suggestions for an
unrecognised symbol, `500` with an `error` message if a provider fails.

## Response shape

```jsonc
{
  "symbol": "RELIANCE.NS",
  "debateRounds": 2,
  "analysts":  [{ "key": "technical", "score": 74, "stance": "bullish",
                  "confidence": 88, "evidence": [], "concerns": [],
                  "coverage": { "missingFields": [] } }],
  "consensus": { "score": 71, "stance": "bullish", "agreement": 75 },
  "conflicts": [{ "gap": 42, "left": {}, "right": {}, "resolution": "..." }],
  "debate":    { "edge": 46, "winner": "bull", "exchanges": [],
                 "unresolved": [], "managerVerdict": {},
                 "bullFactors": [{ "factor": "growth", "label": "Growth",
                                   "points": 2, "contribution": 11.6 }],
                 "bearFactors": [], "deskVotes": [] },
  "proposal":  { "action": "Accumulate", "entryZone": {}, "invalidation": 0,
                 "targets": [], "riskReward": 0 },
  "risk":      { "members": [], "selected": "balanced",
                 "positionSizePercent": 8.4 },
  "decision":  { "status": "Approved with conditions", "action": "Accumulate",
                 "positionSizePercent": 5.0,
                 "deskConfidence": { "score": 71, "label": "Moderate",
                                     "independentDrivers": 5,
                                     "contributingDesks": 3 },
                 "blocking": [], "conditions": [] },
  "grounding": { "score": 82, "verified": 6, "total": 8, "checks": [],
                 "missing": [] }
}
```

## Notes

- Position size is a percentage of a notional book, derived from the risk budget
  and the stop distance. It is a sizing illustration, not an instruction.
- `Hold`, `Reduce`, and `Avoid` always report a size of `0`. A `Hold` or `Avoid`
  that is not blocked reports `Stand aside` rather than `Approved`, because
  approving a decision to commit no capital reads as a green light.
- Research output only. This tab does not provide personalized financial advice.
