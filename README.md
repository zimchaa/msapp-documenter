# msapp-documenter

A [Claude Code](https://claude.com/claude-code) skill that turns a Power Apps canvas app export (`.msapp`, or a Power Platform solution `.zip` containing one) into documentation for the people who maintain it, and a context pack for an AI rebuilding it as a code app.

## What you get

- **PowerPoint deck** built on a swappable company template (theme colours and fonts only, standard layout names). It covers:
  - at-a-glance facts
  - architecture and screen navigation diagrams
  - a wireframe of each screen, with numbered callouts and a "what it does" key
  - the data model, variables and collections, connectors and flows
  - possible defects and areas to fix
  - a rebuild guide
- **Markdown document** with Mermaid diagrams (architecture, navigation, ER data model), SVG screen wireframes and full formulas.
- **`ai_context/` pack**: small chunks with front matter, one per screen, plus `app_model.json`. Each screen chunk includes a text layout sketch, for an AI agent rebuilding the app.
- **Clean source**: pa.yaml-style YAML per screen, with layout, static styling, default values and empty handlers removed. On a typical app this cuts thousands of property formulas down to the hundred or so that carry behaviour.

## How it works

1. **Extract (deterministic).** Python scripts unzip the `.msapp`, read the classic `Controls/*.json` trees (or `Src/*.pa.yaml`), `DataSources.json` (SharePoint/Dataverse schemas, connectors, flows) and the App Checker SARIF. They then clean the controls, analyse symbols, usage and navigation, evaluate layout formulas into wireframes, and run rule-based checks. The same input always gives byte-identical output; `document_app.py check` proves it.
2. **Interpret (Claude).** Claude reads the chunks and writes `interpretation.json`: purpose, user journeys, notes per screen and per control, a review of each finding, extra defects, and a rebuild guide.
3. **Render.** The deck, the Markdown and the AI pack are regenerated with the interpretation merged in. Interpreted content is always labelled *AI interpretation*.

Power Automate flow definitions are not stored in a `.msapp`. Pass the solution export `.zip` to have flows documented from `Workflows/*.json`.

## Install

Clone into your Claude Code skills folder:

```bash
git clone https://github.com/zimchaa/msapp-documenter ~/.claude/skills/msapp-documenter
bash ~/.claude/skills/msapp-documenter/scripts/setup.sh   # creates a venv with python-pptx + PyYAML
```

Then ask Claude Code something like *"document this app: ~/Downloads/MyApp.msapp"*.

## Run the scripts directly

```bash
PY=~/.local/share/msapp-documenter/venv/bin/python
$PY scripts/document_app.py extract MyApp.msapp --out out/      # facts, clean YAML, wireframes, AI chunks
# write out/interpretation.json (see references/interpretation_schema.md), then:
$PY scripts/document_app.py render --out out/ [--template company.pptx]
$PY scripts/document_app.py check MyApp.msapp                   # determinism check
```

Requirements: Python 3.10+, `python-pptx`, `PyYAML`. LibreOffice is optional, for previewing slides as images.

## Limitations

- The wireframes are approximate. Formulas that depend on data or variables are drawn dashed, and components and complex classic forms are drawn approximately.
- The rule-based findings are pattern matches. The AI review step is there to confirm or dismiss them.
- Solution-zip flow parsing and pa.yaml-only `.msapp` files are less tested than classic `.msapp` exports.

## Licence

MIT, see [LICENSE](LICENSE).
