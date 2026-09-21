# Working agreements for Claude Code on Picasso LandMesh

Read at the start of every session. Keep this file short; details belong in `README.md`, `docs/` and
the help pages.

## 1. Log every prompt (standing rule)

**Append an entry to [PROMPTS.md](PROMPTS.md) for every instruction the user gives, in the same turn,
before writing the reply.** One entry per user message, in order, with:

* a numbered heading holding the prompt **verbatim** (spelling, typos and all, in quotes);
* **Interpretation.** what was understood, the judgement calls made, and anything deliberately left
  out or assumed;
* **Outcome.** what was actually built, changed or answered.

Rules:

* Questions and one-word replies ("Ok", "Let us commit") get an entry too; the outcome then records
  the answer or the decision, not code.
* A correction given mid-task is its own entry, not an edit of the previous one.
* Start a new `## Session N — <date>` heading when a session begins, with a one-line subject.
* Never rewrite earlier entries: the file is a history, so mistakes and reversals stay visible.
* Slash commands and other local commands typed into the CLI are not prompts and are not logged.

## 2. Committing

Commit only when the user asks. Never commit on your own initiative, and treat "do not commit
anything" as binding for the whole task. The user often commits their own way (`git log`), so check
`git status` before claiming anything about what is or is not committed.

## 3. Verify before reporting

Before saying a piece of work is done, run what proves it:

```
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check --select F,E9 plm tests
cd web && npm run build                 # tsc --noEmit + vite build
node e2e/road_check.mjs http://127.0.0.1:8000 <projectId>
node e2e/help_check.mjs http://127.0.0.1:8000
node e2e/visitor_check.mjs http://127.0.0.1:8000
node e2e/keys_check.mjs http://127.0.0.1:8000 <terrainProjectId> <roadProjectId>
node e2e/tools_check.mjs http://127.0.0.1:8000 <roadProjectId>
node e2e/tin_edges_check.mjs http://127.0.0.1:8000 <terrainProjectId>
node e2e/line_edit_check.mjs http://127.0.0.1:8000 <terrainProjectId>
node e2e/selection_check.mjs http://127.0.0.1:8000 <terrainProjectId> <roadProjectId>
```

Report failures with their output. Do not describe a result you have not seen.

## 4. Environment traps that have already cost time

* **Bash heredocs mangle backslashes** here: `\\` collapses to `\` and `\b` becomes a backspace
  character. Write patch scripts to the scratchpad with the Write tool and run them by path, or use
  the Edit tool. After patching README-style text, assert that no `\x08` is present.
* Start the dev server with the absolute interpreter path
  (`C:/se/MyEngineeringProjects/plm/.venv/Scripts/python.exe -m plm.serve`); a relative path from
  `web/` exits 127. Stop the old background task before starting a new one, or the port is taken.
* Playwright: the 3-D map never settles, so use `dispatchEvent("click")` and
  `animations: "disabled"` on screenshots instead of waiting for actionability.

## 5. Domain conventions

* **Standards are data.** Design values live in `plm/design/standards/*.json` with a `source` clause
  and a `status` (`verified`, `default`, `placeholder`, `deviation`). Checks never embed numbers; they
  call `Standard.resolve()` and report the value with its source. `nrs-2070.json` is the Nepal Road
  Standard 2070 transcribed from `Resource/Nepal Road Standard (NRS) 2070.pdf`; application defaults
  that the standard does not give are marked `default`, not `verified`.
* **The terrain is read-only to designs.** A design is pinned to one TIN run; rebasing is explicit.
* **Geometry stays out of the UI.** `plm/engine` and `plm/design` know nothing about HTTP or the
  browser; the API layer joins them to the stores.
* **Drawings have one source of truth.** Sheets are built once as primitives in millimetres and
  rendered to both DXF and SVG, so the browser preview and the AutoCAD file cannot drift apart.
* After a UI change that appears in the help pages, re-run `node e2e/help_shots.mjs` so the
  screenshots still match the software.

## 6. Selection and properties (design rule)

**If it can be picked, it has properties; if it has properties, they are shown and edited in one
place.** `web/src/ui/selection.ts` holds the contract. An element does not get its own panel: it
describes itself as a `Selection` and the single inspector renders it.

```ts
selectElement({
  kind: "wall", id: 3, label: "Retaining wall (left)",
  subtitle: "0+040 – 0+120 · gabion",
  fields: [{ key: "height", label: "Height", value: 4.5, type: "number", unit: "m", step: 0.1 }, ro("Length", 80, "m")],
  apply: (v) => { ...; return "Wall updated — save the structures to keep it"; },
  actions: [{ label: "Show", run: () => ws.setStation(st.from) }],
});
```

Rules for anything new:

* **One contract.** Survey points, constraint vertices, IPs, PVIs, walls, drains, culverts and
  whatever comes next all report a `Selection`. Adding an element type means writing a provider, not
  a panel. If you find yourself building a bespoke properties form, you are doing it wrong.
* **Every field carries its unit** (`m`, `%`, `m³`) and reads as the engineer says it: *Radius R*,
  *Transition Ls*, *Easting*. A field the user cannot change is `readonly` (`ro()`), not a disabled
  box.
* **Say what a change costs.** `apply` returns the sentence for the toast, and it must state the
  consequence where there is one - "save the alignment to keep it", "rebuild the TIN to use it". A
  silent edit that quietly invalidates a downstream result is a bug.
* **Nothing is written on selection.** Picking an element never changes data; only `apply` does.
* **One panel, not many.** The inspector is mounted once per workspace (`mountInspector(host, opts)`)
  and torn down with it (`clearSelection()` on destroy). In the terrain workspace it is the
  **Properties** tab of the layer panel, not a second card over the map; a new selection brings that
  tab forward.

Related: constraint lines (breaklines, boundaries, voids) are **plan geometry**. The vertex editor
asks for Easting and Northing only - a constraint vertex whose Z is 0 gets its level interpolated
from the survey surface by the engine (`surface_z`). A breakline imported with surveyed levels keeps
them on every vertex an edit does not move.

## 7. Writing for the user

The user is a civil and road engineer in Nepal, not a Python developer. Explain in engineering terms,
name the clause or table behind a rule, and keep code out of prose unless the file is the point.
