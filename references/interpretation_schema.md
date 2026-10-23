# interpretation.json

Written by the AI in step 2, saved as `<out_dir>/interpretation.json`. Every key is optional — renderers skip
what's missing — but a complete file gives the best documents. Plain text only (no Markdown) in string values;
keep them concise because they land on slides: aim for the lengths noted.

```json
{
  "summary": "2-4 sentences: what the app does, for whom, and the end result (e.g. a SharePoint item created).",
  "audience": "1 sentence: who uses it and in what context (Teams tab, mobile, etc.).",
  "user_journeys": [
    {"name": "Request a new team", "steps": ["Enter team name, description and privacy", "Pick members", "..."]}
  ],
  "screens": {
    "<exact screen name>": "2-3 sentences: purpose, what the user does, what data it reads/writes, anything surprising."
  },
  "controls": {
    "<exact screen name>": {
      "<exact control name>": "1-2 sentences of context the formulas alone don't give: where the value ends up, business meaning, gaps or risks."
    }
  },
  "data_notes": "3-6 sentences: what the lists/tables represent, how app data maps to columns, columns the app ignores, data quality risks.",
  "integration_notes": "1-3 sentences on connectors/flows: what they're used for and assumptions (licensing, permissions).",
  "maintainer_notes": ["Short actionable tip, e.g. 'Screens are copies: change the stepper in all six or refactor to a component first.'"],
  "finding_reviews": {
    "F01": {"verdict": "confirmed | likely | needs-check | false-positive", "note": "1-2 sentences why", "severity": "optional override: High | Medium | Low | Info"}
  },
  "additional_findings": [
    {
      "title": "Short defect title",
      "severity": "High | Medium | Low | Info",
      "category": "Correctness | Reliability | Data / scale | Security | Maintainability | Usability | Quality | Performance | Accessibility",
      "where": ["Screen › Control.Property"],
      "evidence": "The exact formula fragment or text that shows it",
      "recommendation": "What to change, 1-2 sentences"
    }
  ],
  "rebuild": {
    "overview": "2-4 sentences: the capability to rebuild, independent of Power Apps.",
    "suggested_architecture": "1-3 sentences: e.g. single-page form wizard + API writing to the same SharePoint list via Graph.",
    "entities": [{"name": "TeamsRequest", "fields": ["Title (required)", "..."], "notes": "how the app populates it"}],
    "features": [{"name": "Members picker", "behaviour": "Search directory users, multi-select", "source": "Step 2 Screen › Team Members ComboBox"}],
    "business_rules": ["Team name max 30 chars", "Channels 2 and 3 default to 'N/A' when blank"],
    "avoid": ["Pattern from the app not to replicate, and why"]
  },
  "open_questions": ["Things the .msapp can't answer that a maintainer should confirm"],
  "glossary": {"Term": "Meaning in this app"}
}
```

Guidance:

- `screens` keys must match screen names exactly (see `ai_context/index.md`).
- `controls`: one entry per numbered callout in each screen chunk's "Key to numbered controls" table
  (use the exact control names). Don't repeat the deterministic "What it does" text; add what a maintainer
  can't see from it: the business meaning, where the value is finally stored, missing validation, surprises.
  Shown as the "AI context" column next to the wireframe key.
- `finding_reviews` keys are the IDs in `ai_context/04-findings.md` (F01…). Review all of them.
  `false-positive` moves a finding to a "Dismissed on review" list (still visible for transparency).
- `additional_findings` get IDs A01… in order. Only include issues with concrete evidence.
- Severity guide: **High** = data loss/corruption, security exposure, or a core journey broken;
  **Medium** = likely bugs or failures under realistic conditions (scale, errors, renames);
  **Low** = maintainability, polish, accessibility gaps; **Info** = worth knowing.
