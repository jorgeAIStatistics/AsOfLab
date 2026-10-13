# AsOfLab inspection UI

A small React + TypeScript + Vite app that reads the local, read-only AsOfLab API
(`src/asoflab/api/app.py`) and shows what a run computed. It has no mock data: every
number on screen comes from the API, and a missing result is shown as missing, with
the command that generates it.

All data are synthetic. The UI says so on every view.

## Views

Switched by the top navigation and kept in the URL hash, so a view can be linked.

| Hash | View | Reads |
| --- | --- | --- |
| `#/overview` | Run overview: policies, provenance, audit findings, experiment A | `manifest`, `statistics`, `audit` |
| `#/timeline` | Patient timeline with a draggable prediction cutoff | `patients`, `patients/{id}`, `patients/{id}/timeline` |
| `#/comparison` | Paired metric differences; as-known and naive features side by side | `statistics`, `comparison`, `engineering` |
| `#/engineering` | Engine agreement, seeded bugs, crash recovery, benchmarks, commands | `audit`, `engineering` |

The run selector in the header applies to the first three views and to the engine
agreement table.

## Run it

Requires Node 22.12 or newer (developed with Node 24).

```bash
cd web
npm ci
```

### Development

Start the API in one terminal, from the repository root. The dev server expects it on
port 8010:

```bash
asoflab serve --runs runs/demo --results results --port 8010
```

Then, in `web/`:

```bash
npm run dev        # http://localhost:5173, proxies /api to http://127.0.0.1:8010
```

To use another API port, change `API` in `vite.config.ts`.

### Production build

```bash
npm run build      # tsc --noEmit (strict), then vite build into web/dist
```

`asoflab serve` serves `web/dist` at `/` when it exists (`--web web/dist` is the
default), so after a build one process serves both the UI and the API:

```bash
asoflab serve --runs runs/demo --results results
```

The UI uses relative `/api/...` URLs only.

### Other scripts

```bash
npm run typecheck  # tsc --noEmit
npm test           # vitest: formatting, scales, lane packing, URL and error handling
npm run preview    # serve web/dist with the same /api proxy as the dev server
```

## Layout

```
src/
  api.ts            types for the API's JSON, typed endpoints, error handling
  hooks.ts          useLoad (fetch with pending/failed states), hash routing, debounce
  format.ts         numbers, intervals, times; a missing value is never shown as 0
  scale.ts          ticks, linear/log/time scales, lane packing
  labels.ts         plain-language names for policies, metrics, comparisons, statuses
  styles.css        colour tokens and look of the static report (light and dark)
  App.tsx           header, run selector, navigation
  views/            one file per view
  components/       shared tables, async states, metric tables, benchmark section
  charts/           hand-written inline SVG: forest plot, line chart, bar chart, timeline
```

## Conventions

- **Dependencies.** `react` and `react-dom` at run time; Vite, TypeScript and Vitest
  for development. No chart library, CSS framework or router. Versions are pinned
  exactly in `package.json`.
- **Charts** follow `src/asoflab/audit/svg.py`: 2 px lines, markers with a
  surface-coloured ring, hairline gridlines, one y-axis, text in text colours, a
  legend for two or more series, a native tooltip on every mark, and a table of the
  same numbers next to each chart. Series colours are fixed per entity: `--series-1`
  as-known/batch, `--series-2` naive/replay, `--series-3` oracle.
- **Missing values.** An interval whose `point` is null renders as "not estimable"
  with the `reason` from the file. A value that is not zero but would round to zero
  is printed in scientific notation, so the sign of a small difference or of an
  interval bound is not rounded away.
- **Status is never colour alone.** Every eligibility status on the timeline has its
  own shape, repeated with its name in the legend and the details table.
- **Errors.** A failed request shows the API's `detail.message`; `detail.technical`
  is inside a collapsed "Technical details" block.
- **Time.** Every time is UTC and is labelled as such. Cutoffs are sent to the API as
  ISO-8601 with an explicit offset.

## Timeline controls

- Drag the cutoff line (mouse or touch), or click the time axis.
- With the cutoff focused: left/right arrows move it 15 minutes, with Shift 1 minute,
  Page Up/Page Down 1 hour, Home/End to the edges of the visible range.
- The triangles above the chart, the select, and the previous/next buttons jump to
  the patient's real prediction times.
- A change is sent to `/timeline` once the cutoff has been still for 120 ms.
