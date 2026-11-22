# CHAT_MARKERS.md — the markers the cockpit renders in chat

An agent's conversation reply may carry a marker: a bracketed tag with JSON
inside, which the cockpit strips from the visible text and renders as a
component under the message. Nothing in the runtime interprets a marker; the
agent learns the syntax from a generated section of its charter
(`central_command/runtime/agent.py`, `CHART_MARKERS` and
`GOAL_PREVIEW_MARKERS`, appended to every charter by `build_charter`), and the
cockpit's parsers are the contract. Keep this page, those constants and the
parsers in step: the guard test proves the charter section is present, not
that the cockpit still parses what it says.

Markers belong in conversation replies only, never in proposal arguments, task
output or graph episodes. Put each on its own line, with valid JSON inside, and
introduce it in the surrounding text, since the marker itself is not shown.

## `[chart:{...}]` — an inline chart

```
[chart:{"type":"<type>","title":"<title>","data":{...}}]
```

`title` is optional. The parser (`web/src/features/charts/extractCharts.ts`,
`extractChartMarkers`) scans for the balanced closing `}]`, so nested JSON is
fine; a marker whose JSON does not parse or fails validation is dropped.
Validation requires a known `type`, a `data` object with a `labels` array, and
at least one of `values`, `series` or `candles` (each an array when present).

| `type` | Data | Renderer |
|---|---|---|
| `line`, `area` | `labels` + `values`, or `series: [{"name","values"}, ...]` for several lines | `LightweightChart.tsx` |
| `candle` | `labels` + `candles: [{"open","high","low","close"}, ...]` | `LightweightChart.tsx` |
| `bar`, `pie` | `labels` + `values` | Recharts, in `InlineChart.tsx` |
| `tv` | no `data`; `symbol` (for example `"TVC:GOLD"`) and optional `interval` (`1`, `5`, `15`, `60`, `D`, `W`, `M`; default `W`) | `TradingViewWidget.tsx`, which loads TradingView's script from the internet |

Agents are told about `line`, `area`, `candle`, `bar` and `pie`, with short
labels and 3 to 12 data points. The parser also accepts `tv`, but no charter
mentions it, and it needs internet access, so an air-gapped cockpit cannot
render it.

```
[chart:{"type":"line","title":"Monthly Revenue","data":{"labels":["Jan","Feb","Mar"],"values":[4200,5800,4900]}}]
[chart:{"type":"line","title":"Growth","data":{"labels":["Q1","Q2","Q3"],"series":[{"name":"Users","values":[100,250,480]},{"name":"Revenue","values":[10,35,90]}]}}]
[chart:{"type":"bar","title":"Tickets by Queue","data":{"labels":["Mail","Jira","Calendar"],"values":[52,38,29]}}]
```

## `[goal-preview:{...}]` — a draft goal tile

```
[goal-preview:{"name":"Run three times a week","cadence":"weekly","questions":["How many runs this week?"],"thresholds":"3+ = green, 1-2 = yellow"}]
```

Shown while the operator and an agent discuss a goal, before anything is
proposed. `name` is required and non-empty; `cadence` and `thresholds` are
optional strings (agents are told `cadence` is `"weekly"` or `"monthly"`) and
`questions` an optional list of strings. It is a preview and nothing more: it
creates no goal, and only an approved `loe.create` proposal does. Parser:
`web/src/features/loe/extractGoalPreviews.ts`; renderer:
`web/src/features/loe/GoalPreviewCard.tsx`, which the Decisions Inbox also uses
for a `loe.create` proposal's arguments.

## Order of processing

The cockpit strips `[tts:...]` markers first (an inherited speech feature no
charter mentions, so it is not documented here), then charts, then goal
previews (`web/src/features/chat/operations/streamEventHandler.ts` and
`loadHistory.ts`); the remaining text renders as Markdown.
