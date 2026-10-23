# Using a company PowerPoint template

Pass any `.pptx` or `.potx`-derived `.pptx` with `render --template company.pptx`. The renderer:

- **Removes any slides** already in the template, keeping masters, layouts, theme, logos and footers.
- **Finds layouts by name**: `Title Slide` (cover), `Section Header` (dividers), `Title Only` (all content
  slides; falls back to `Title and Content`, then to the layout at position 6). Rename layouts in
  View → Slide Master if the template uses other names.
- **Draws content in the area below the title placeholder**, so keep the title near the top of `Title Only`.
- **Uses theme colours only** (Accent 1 = screens, Accent 2 = data, Accent 3 = start screen/trigger,
  Accent 4 = flows, Accent 5 = AI-interpretation tags and findings tile, Accent 6 = connectors/back links),
  and never sets fonts, so the deck takes on the template's palette and typography automatically.
  The only fixed colours are severity tiles (red/amber/grey) and grey type labels.
- Works with any slide size; 16:9 is recommended.

Checklist for a good result:

1. Template has the three layouts named above (or close equivalents).
2. Theme Accent 1–6 are distinct from each other and readable with white text (they're used as box fills).
3. Template's default table style is legible at 10pt.
4. Run once, convert to PDF and look at the navigation, data model and findings slides.

To change the built-in default, replace `assets/default_template.pptx` (or regenerate it with
`scripts/make_default_template.py`).
