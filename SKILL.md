---
name: msapp-documenter
description: Turn a Power Apps canvas app export (.msapp, or a Power Platform solution .zip containing one) into maintainer documentation - a PowerPoint deck built on a swappable company template, a Markdown file with Mermaid diagrams (data model/ER, screen navigation, screens-to-data/flows), and an AI-ingestible context pack for rebuilding the app as a code app - plus a "possible defects and areas to fix" review. Use this whenever the user mentions a .msapp file, a canvas app export, documenting / reverse-engineering / auditing / handing over a Power App, understanding the SharePoint lists or Dataverse tables behind an app, migrating or rebuilding a Power App in code, or wants the YAML/formulas out of an app - even if they don't say "documentation".
---

# msapp-documenter

Documents a Power Apps canvas app from its `.msapp` file in two layers:

1. **Deterministic extraction** (scripts): unpack, clean, analyse and render. Same input → byte-identical output. This is the trustworthy layer: every fact is traceable to the app's own formulas/metadata.
2. **AI interpretation** (you): read the small, clean chunks the scripts produce and write `interpretation.json` — purpose, user journeys, per-screen explanations, a review of the rule-based findings, extra defects the rules can't see, and a rebuild guide. The renderers merge it in, always labelled *AI interpretation*, so readers can tell inference from fact.

Outputs serve two audiences: a **human maintainer** (the deck + Markdown) and an **AI coding agent** rebuilding the functionality as a code app (`ai_context/` + the Markdown's rebuild guide).

## What's inside a .msapp (so you know what's possible)

A `.msapp` is a zip. Classic exports contain **no YAML**: each screen is a big JSON tree in `Controls/*.json` (~95% of it embedded control definitions), with `References/DataSources.json` (SharePoint/Dataverse column schemas, connectors, flows, collections), `Properties.json`, `Header.json` and `AppCheckerResult.sarif` (Microsoft's own App Checker results). Newer exports may add `Src/*.pa.yaml`. The scripts handle both and emit clean pa.yaml-style source themselves.

**Not in a .msapp:** Power Automate flow definitions (only their names/signatures) and data rows. If the app calls flows and the user wants their steps documented, ask for the **solution export .zip** — pass it instead of (or as well as containing) the .msapp; flows are read from `Workflows/*.json`.

## Workflow

Scripts live in `scripts/`; run them with the skill's Python environment:

```bash
SKILL=<path to this skill>
bash $SKILL/scripts/setup.sh          # once: creates ~/.local/share/msapp-documenter/venv (python-pptx, PyYAML)
PY=~/.local/share/msapp-documenter/venv/bin/python
```

### 1. Extract (deterministic)

```bash
$PY $SKILL/scripts/document_app.py extract <app.msapp | solution.zip> --out <out_dir> [--app <name filter>]
```

Default `<out_dir>`: a folder named after the app next to the input file, unless the user says otherwise. It writes:

- `source/App.yaml`, `source/screens/NN-<screen>.yaml` — clean control trees (only behaviour, data bindings and dynamic properties; layout, static styling, defaults and empty handlers removed)
- `diagrams/screens/NN-<screen>.svg` — broad-strokes wireframe of each screen, with numbered pink markers on the significant controls (buttons, inputs, galleries, computed text) that key into a "what it does" table at the app's design size (layout formulas are evaluated for that screen; auto-layout, galleries and forms are simulated; anything unresolvable is drawn dashed as approximate)
- `ai_context/` — `index.md`, overview / data-model / integrations / findings / rebuild chunks, one chunk per screen (facts + clean YAML), `app_model.json`
- `<App>_documentation.md` — facts-only draft

Report the summary line it prints (screens, controls, findings, formulas kept vs total).

### 2. Interpret (you)

Read `ai_context/index.md`, then every chunk (they are small by design — read them all, screens included, rather than sampling). Then write `<out_dir>/interpretation.json` following `references/interpretation_schema.md` exactly.

What makes the interpretation valuable:

- **Ground every claim** in something you read: name the control/property (`Submit Screen › Submit - Next Button.OnSelect`). If you infer intent, say so ("appears to…"). Never invent flow steps, data volumes or users you can't see.
- **Explain for a maintainer who has never opened the app**: what each screen is for, how data moves from inputs → collections/variables → data source, what breaks if they change X.
- **Review every rule finding** (`finding_reviews`): confirm, adjust severity, or mark `false-positive` with a reason. Rules are pattern-based; you have the context to judge them.
- **Add findings the rules can't detect** (`additional_findings`): logic bugs (e.g. a field written that is never validated, wrong field mapped in a Patch, Reset() of the wrong control), typos in user-facing text, hard-coded personal names, missing validation (required SharePoint columns not enforced in the UI), dead branches, inconsistent data shapes (e.g. Person column written with blank Department/JobTitle), security/permission assumptions. Each needs `where` + `evidence` + `recommendation`.
- **Annotate the numbered controls** (`controls` in the schema): each screen chunk has a "Key to numbered controls" table matching pink markers on the wireframe. Give each one a short AI-context note.
- **Use the layout sketch** in each screen chunk (and, if useful, look at the SVG wireframes): wrong or copy-pasted labels, controls that overlap or sit off-screen, and inconsistent layouts between screens are easy to spot there and invisible in formulas.
- **Rebuild guide**: describe behaviour, not Power Fx. Entities, features (with the source control/property), business rules and validation, and what *not* to copy. Assume the reader is an AI agent building a modern code app against the same data.

### 3. Render

```bash
$PY $SKILL/scripts/document_app.py render --out <out_dir> [--template <company.pptx>]
```

Produces `<App>_documentation.pptx` (including a native-shape wireframe slide per screen) and regenerates the Markdown and `ai_context/` with the interpretation merged in. If the user has a company template, pass it with `--template` (see `references/pptx_template.md` for what makes a template work well); otherwise `assets/default_template.pptx` is used.

### 4. Check and hand over

- Visually check the deck when LibreOffice is available: `soffice --headless --convert-to pdf` then `pdftoppm -r 50 -png` and look at a few slides (overview, navigation, a screen, data model, findings). Fix overflowing text by shortening your interpretation text rather than editing the deck by hand — hand edits are lost on re-render.
- Tell the user where the files are, the headline numbers, the top 3 findings, and anything you couldn't determine (e.g. flow internals without a solution export).

## Reproducibility

Steps 1 and 3 are pure functions of their inputs (`.msapp`, `interpretation.json`, template). To prove it: `$PY $SKILL/scripts/document_app.py check <app.msapp>` runs extraction twice and compares hashes. Keep `interpretation.json` alongside the outputs — it is the only non-deterministic artefact, and re-rendering from it reproduces the deck exactly. When an app changes, re-extract, then update (don't rewrite) the interpretation: findings IDs are stable for an unchanged app but may renumber when findings are added or removed, so re-check `finding_reviews` keys against `ai_context/04-findings.md`.

## Files

- `scripts/document_app.py` — CLI (extract / render / check)
- `scripts/msapp_model.py` — unzip + parse + clean (property filtering rules live here)
- `scripts/msapp_analyze.py` — symbols, usage, navigation, data model, rule-based findings
- `scripts/wireframe.py` — layout-formula evaluator, wireframe boxes, SVG + text outline
- `scripts/render_md.py`, `scripts/render_pptx.py` — renderers; `scripts/fx.py` — Power Fx text helpers
- `references/interpretation_schema.md` — the JSON you write in step 2 (read before writing it)
- `references/pptx_template.md` — making a company template work
- `assets/default_template.pptx` — neutral 16:9 template (regenerate with `scripts/make_default_template.py`)
