"""Render the Markdown documentation and the AI-context chunk pack from app_model.json (+ interpretation.json).

Facts come from the deterministic model. Anything from interpretation.json is
fenced as AI interpretation so readers (human or AI) can tell the two apart.
"""
import json
import os
import re

AI_TAG = "> **AI interpretation** (inferred from the app's formulas and metadata; verify before relying on it)"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "item"


def mid(prefix, i):
    return f"{prefix}{i}"


def mlabel(s):
    return (s or "").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def mword(s):
    w = re.sub(r"\W+", "_", s or "").strip("_")
    return w if w and not w[0].isdigit() else "c_" + w


def code(formula, lang="powerfx"):
    return f"```{lang}\n{formula}\n```"


def table(headers, rows):
    if not rows:
        return "_None._"
    esc = lambda v: str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def bullets(items):
    return "\n".join(f"- {i}" for i in items) if items else "_None._"


# ----------------------------------------------------------------------------- diagrams

def mermaid_navigation(m):
    a = m["analysis"]
    names = [s["name"] for s in m["screens"]]
    ids = {n: mid("S", i) for i, n in enumerate(names)}
    lines = ["flowchart LR"]
    for n in names:
        shape = f'{ids[n]}(["{mlabel(n)}"])' if n == a["start_screen"] else f'{ids[n]}["{mlabel(n)}"]'
        lines.append("  " + shape)
    back = False
    for e in a["navigation"]:
        if e["from"] not in ids or e["from"] == e["to"]:
            continue
        if e["to"] not in ids:
            back = True
            lines.append(f'  {ids[e["from"]]} -.->|Back| BACK(("previous"))')
            continue
        arrow = "-.->" if e["conditional"] else "-->"
        lines.append(f"  {ids[e['from']]} {arrow} {ids[e['to']]}")
    if a["start_screen"] in ids:
        lines.append(f'  START((" ")) --> {ids[a["start_screen"]]}')
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def mermaid_architecture(m):
    a = m["analysis"]
    lines = ["flowchart LR"]
    sid = {s["name"]: mid("S", i) for i, s in enumerate(m["screens"])}
    lines.append('  subgraph APP["App screens"]')
    for n, i in sid.items():
        lines.append(f'    {i}["{mlabel(n)}"]')
    lines.append("  end")
    nodes, edges = {}, []
    for j, d in enumerate([d for d in m["data_sources"] if d["kind"] != "Collection"]):
        u = a["data_usage"].get(d["name"], {})
        if not u.get("read_in") and not u.get("written_in"):
            continue
        did = mid("D", j)
        label = f"{mlabel(d.get('display_name') or d['name'])}<br/><i>{mlabel(d['kind'])}</i>"
        if d["kind"] == "Power Automate flow":
            nodes[did] = f'{did}{{{{"{label}"}}}}'
        elif d["kind"] == "Connector":
            nodes[did] = f'{did}[/"{mlabel(d["connector"] or d["name"])}<br/><i>connector</i>"/]'
        else:
            nodes[did] = f'{did}[("{label}")]'
        for s in a["screens"]:
            reads = d["name"] in s["data_sources"] + s["flows"] + s["connectors"]
            writes = d["name"] in s["data_written"]
            if writes:
                edges.append(f"  {sid[s['name']]} ==>|writes| {did}")
            elif reads:
                verb = "runs" if d["kind"] == "Power Automate flow" else ("calls" if d["kind"] == "Connector" else "reads")
                edges.append(f"  {sid[s['name']]} -->|{verb}| {did}")
        if any(w.startswith("App.") for w in u.get("read_in", []) + u.get("written_in", [])):
            edges.append(f"  APP -->|on start| {did}")
    if nodes:
        lines.append('  subgraph EXT["Data & services"]')
        lines += ["    " + v for v in nodes.values()]
        lines.append("  end")
    lines += edges
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def mermaid_er(m):
    a = m["analysis"]
    lines = ["erDiagram"]
    any_entity = False
    for d in m["data_sources"]:
        if not d.get("columns") or d["is_sample"]:
            continue
        used = set(a["data_usage"].get(d["name"], {}).get("columns_used", []))
        cols = [c for c in d["columns"] if not c["system"] or c["primary_key"] or c["name"] in used]
        lines.append(f"  {mword(d.get('display_name') or d['name'])} {{")
        for c in cols:
            flags = " PK" if c["primary_key"] else ""
            notes = []
            if c["required"]:
                notes.append("required")
            if c["name"] in used:
                notes.append("used by app")
            if c["read_only"] and not c["primary_key"]:
                notes.append("read-only")
            note = f' "{c["display_name"] if c["display_name"] != c["name"] else ""}{" - " if c["display_name"] != c["name"] and notes else ""}{", ".join(notes)}"' if notes or c["display_name"] != c["name"] else ""
            lines.append(f"    {mword(c['type'])} {mword(c['name'])}{flags}{note}")
        lines.append("  }")
        any_entity = True
    for c in a["collections"]:
        if c["inferred_fields"]:
            lines.append(f"  {mword('collection_' + c['name'])} {{")
            lines += [f"    field {mword(fl)}" for fl in c["inferred_fields"]]
            lines.append("  }")
            any_entity = True
    return "```mermaid\n" + "\n".join(lines) + "\n```" if any_entity else "_No tabular data sources with a schema._"


def mermaid_flow(flow):
    lines = ["flowchart TD"]
    for i, t in enumerate(flow["triggers"]):
        lines.append(f'  T{i}(["Trigger: {mlabel(t["name"])}<br/><i>{mlabel(t["kind"] or t["type"])}</i>"])')
    prev = "T0" if flow["triggers"] else None
    for i, act in enumerate([x for x in flow["actions"] if x["depth"] == 0]):
        label = mlabel(act["name"].replace("_", " "))
        sub = mlabel(act["operation"] or act["type"])
        lines.append(f'  A{i}["{label}<br/><i>{sub}</i>"]')
        if prev:
            lines.append(f"  {prev} --> A{i}")
        prev = f"A{i}"
    return "```mermaid\n" + "\n".join(lines) + "\n```"


# ----------------------------------------------------------------------------- sections

def sec_overview(m, ai):
    info, a = m["app_info"], m["analysis"]
    s = m["stats"]
    out = [f"## 1. Overview", ""]
    if ai.get("summary"):
        out += [AI_TAG, ">", "> " + ai["summary"].replace("\n", "\n> "), ""]
        if ai.get("audience"):
            out += [f"> **Who uses it:** {ai['audience']}", ""]
    rows = [
        ("App name", info["name"]), ("Description", info["description"] or "_(none set)_"),
        ("App type", f"{info['app_type']} ({info['layout']['width']}×{info['layout']['height']}, {info['layout']['orientation']})"),
        ("Created from", info["created_from"] or "-"), ("Last saved (UTC)", info["saved_utc"]),
        ("Studio doc version", info["doc_version"]), ("Start screen", a["start_screen"]),
        ("Data row limit (non-delegable queries)", info["row_limit"]),
        ("Screens / controls", f"{s['screens']} / {s['controls']}"),
        ("Data sources / flows / connectors", f"{s['tables']} / {s['flows']} / {s['connectors']}"),
        ("Variables / collections", f"{s['variables']} / {s['collections']}"),
        ("Findings", f"{s['findings']} ({s['findings_by_severity']})"),
        ("Source file", f"`{m['source']['file']}` (sha256 `{m['source']['sha256'][:12]}…`, {m['source']['format']})"),
    ]
    out += [table(["Property", "Value"], rows), ""]
    if ai.get("user_journeys"):
        out += ["### User journeys", "", AI_TAG, ""]
        for j in ai["user_journeys"]:
            out.append(f"**{j.get('name', '')}**")
            out += [f"{k}. {st}" for k, st in enumerate(j.get("steps", []), 1)]
            out.append("")
    return "\n".join(out)


def sec_architecture(m, ai):
    out = ["## 2. Architecture", "",
           "How the screens connect to data, flows and connectors (thick arrow = writes).", "",
           mermaid_architecture(m), "", "### Screen navigation", "",
           "Solid arrows are definite `Navigate()` targets; dotted arrows are conditional/possible targets.", "",
           mermaid_navigation(m), ""]
    if m["analysis"]["launch_targets"]:
        out += ["### External links (`Launch`)", "", bullets(f"`{t}`" for t in m["analysis"]["launch_targets"]), ""]
    if m["app_info"]["connections"]:
        out += ["### Connections", "", table(["Connector", "Data sources"],
                                             [(c["connector"], ", ".join(c["data_sources"])) for c in m["app_info"]["connections"]]), ""]
    return "\n".join(out)


def callout_rows(m, screen_name, ai):
    """(# , control, type, what it does, AI context) rows for a screen's wireframe callouts; has_ai flag."""
    sc = next((x for x in m["screens"] if x["name"] == screen_name), {})
    notes = (ai.get("controls") or {}).get(screen_name) or {}
    rows = [(c["n"], c["name"], c["type"], c["does"], notes.get(c["name"], "")) for c in sc.get("callouts", [])]
    return rows, any(r[4] for r in rows)


def callout_table(m, screen_name, ai):
    rows, has_ai = callout_rows(m, screen_name, ai)
    if not rows:
        return []
    if has_ai:
        return ["**Key to numbered controls**", "", table(["#", "Control", "Type", "What it does", "AI context"], rows), ""]
    return ["**Key to numbered controls**", "", table(["#", "Control", "Type", "What it does"], [r[:4] for r in rows]), ""]


def sec_screen(m, s, ai, num, for_chunk=False):
    a = s
    wf = next((x.get("wireframe") for x in m["screens"] if x["name"] == s["name"]), None)
    note = (ai.get("screens") or {}).get(s["name"])
    out = [f"### 3.{num} {s['name']}", ""]
    if note:
        out += [AI_TAG, ">", "> " + note.replace("\n", "\n> "), ""]
    facts = [
        ("Controls", f"{a['control_count']} ({', '.join(a['control_types'])})"),
        ("Reached from", ", ".join(a["navigated_from"]) or ("app start" if s["name"] == m["analysis"]["start_screen"] else "-")),
        ("Goes to", ", ".join(a["navigates_to"]) or "-"),
        ("Data read", ", ".join(a["data_sources"]) or "-"), ("Data written", ", ".join(a["data_written"]) or "-"),
        ("Flows / connectors", ", ".join(a["flows"] + a["connectors"]) or "-"),
        ("Variables set", ", ".join(a["variables_set"]) or "-"), ("Variables read", ", ".join(a["variables_read"]) or "-"),
        ("Collections", ", ".join(a["collections"]) or "-"),
    ]
    if wf and not for_chunk:
        out += [f"![Wireframe of {s['name']}]({wf['svg']})", "",
                "_Broad-strokes wireframe at design size: blue = buttons, outlined = inputs, crossed = images, "
                "dashed = shown conditionally or position approximate, {Field} = data-bound text, ƒx = computed text, "
                "pink numbers = see key._", ""]
    out += callout_table(m, s["name"], ai)
    out += [table(["Aspect", "Detail"], facts), ""]
    if wf and for_chunk:
        import wireframe
        out += [f"**Layout sketch** (design size {wf['width']}x{wf['height']}, top-left origin, absolute pixels)", "",
                "```text", wireframe.outline(wf), "```", ""]
    if a["inputs"]:
        out += ["**User inputs**", "", table(["Control", "Type"], [(i["name"], i["type"]) for i in a["inputs"]]), ""]
    if a["bindings"]:
        out += ["**Data bindings**", ""]
        for b in a["bindings"]:
            out += [f"- `{b['where']}`", "", code(b["formula"]), ""]
    if a["actions"]:
        out += ["**Actions (event handlers)**", ""]
        for b in a["actions"]:
            out += [f"- `{b['where']}`", "", code(b["formula"]), ""]
    out += [f"Clean source: `source/screens/{s['file']}`", ""]
    return "\n".join(out)


def sec_screens(m, ai):
    out = ["## 3. Screens", ""]
    out += [table(["#", "Screen", "Controls", "Inputs", "Data", "Goes to"],
                  [(i, s["name"], s["control_count"], len(s["inputs"]), ", ".join(s["data_sources"] + s["flows"]) or "-",
                    ", ".join(s["navigates_to"]) or "-") for i, s in enumerate(m["analysis"]["screens"], 1)]), ""]
    for i, s in enumerate(m["analysis"]["screens"], 1):
        out.append(sec_screen(m, s, ai, i))
    if m["components"]:
        out += ["### Components", "", bullets(f"{c['name']} (`source/components/{c['file']}`)" for c in m["components"]), ""]
    return "\n".join(out)


def sec_data(m, ai):
    a = m["analysis"]
    out = ["## 4. Data model", ""]
    if ai.get("data_notes"):
        out += [AI_TAG, ">", "> " + ai["data_notes"].replace("\n", "\n> "), ""]
    out += [mermaid_er(m), ""]
    for d in m["data_sources"]:
        if d["kind"] in ("Collection",):
            continue
        u = a["data_usage"].get(d["name"], {})
        out += [f"### {d['name']} ({d['kind']}{', sample data' if d['is_sample'] else ''})", ""]
        meta = []
        if d.get("display_name") and d["display_name"] != d["name"]:
            meta.append(("List/table title", d["display_name"]))
        if d.get("site"):
            meta.append(("Site", d["site"]))
        if d.get("connector"):
            meta.append(("Connector", d["connector"]))
        meta += [("Read in", "; ".join(u.get("read_in", [])[:8]) or "-"), ("Written in", "; ".join(u.get("written_in", [])) or "-")]
        if u.get("operations"):
            meta.append(("Operations used", ", ".join(u["operations"])))
        out += [table(["", ""], meta), ""]
        if d.get("columns"):
            used = set(u.get("columns_used", []))
            rows = [(c["name"], c["display_name"] if c["display_name"] != c["name"] else "", c["type"],
                     "yes" if c["required"] else "", "yes" if c["read_only"] else "", "✓" if c["name"] in used else "",
                     "system" if c["system"] else "")
                    for c in d["columns"]]
            out += [table(["Column", "Display name", "Type", "Required", "Read-only", "Used by app", "Note"], rows), ""]
    out += ["### Variables", "", table(["Name", "Scope", "Set in", "Read in", "Example value"],
                                       [(v["name"], v["scope"], "; ".join(v["set_in"][:4]), "; ".join(v["read_in"][:4]) or "**never**",
                                         f"`{v['example_value']}`" if v["example_value"] else "") for v in a["variables"]]), ""]
    out += ["### Collections", "", table(["Name", "Filled in", "Read in", "Fields (inferred)"],
                                         [(c["name"], "; ".join(c["written_in"][:4]) or "-", "; ".join(c["read_in"][:4]) or "**never**",
                                           ", ".join(c["inferred_fields"])) for c in a["collections"]]), ""]
    return "\n".join(out)


def sec_integrations(m, ai):
    a = m["analysis"]
    out = ["## 5. Flows & integrations", ""]
    if ai.get("integration_notes"):
        out += [AI_TAG, ">", "> " + ai["integration_notes"].replace("\n", "\n> "), ""]
    ext = [d for d in m["data_sources"] if d["kind"] in ("Connector", "Power Automate flow")]
    if ext:
        rows = []
        for d in ext:
            u = a["data_usage"].get(d["name"], {})
            rows.append((d["name"], d["kind"], d["connector"], ", ".join(u.get("operations", [])) or "-",
                         "; ".join(u.get("read_in", [])[:5]) or "not used"))
        out += [table(["Name", "Kind", "Connector", "Operations", "Called from"], rows), ""]
    else:
        out += ["_No connectors or flows are called from the app._", ""]
    if m["solution_flows"]:
        out += ["### Flow definitions (from the solution export)", ""]
        for fl in m["solution_flows"]:
            out += [f"#### {fl['name']}", "", f"Connections: {', '.join(fl['connections']) or '-'}", "", mermaid_flow(fl), ""]
            out += [table(["Step", "Type", "Operation", "Depth"],
                          [(x["name"], x["type"], x["operation"], x["depth"]) for x in fl["actions"]]), ""]
    elif any(d["kind"] == "Power Automate flow" for d in m["data_sources"]):
        out += ["Flow internals are not stored in a .msapp. Re-run with the solution export (.zip) to document their steps.", ""]
    return "\n".join(out)


def merged_findings(m, ai):
    reviews = ai.get("finding_reviews") or {}
    active, dismissed = [], []
    for f in m["analysis"]["findings"]:
        f = dict(f)
        r = reviews.get(f["id"])
        if r:
            f["verdict"], f["review_note"] = r.get("verdict", ""), r.get("note", "")
            if r.get("severity"):
                f["severity"] = r["severity"]
        (dismissed if f.get("verdict") == "false-positive" else active).append(f)
    for i, x in enumerate(ai.get("additional_findings") or [], 1):
        active.append({"id": f"A{i:02d}", "rule": "ai-review", "source": "ai-review", "title": x.get("title", ""),
                       "severity": x.get("severity", "Low"), "category": x.get("category", ""),
                       "where": x.get("where", []), "where_count": len(x.get("where", [])),
                       "evidence": x.get("evidence", ""), "recommendation": x.get("recommendation", "")})
    order = {"High": 0, "Medium": 1, "Low": 2, "Info": 3}
    active.sort(key=lambda f: (order.get(f["severity"], 9), f["id"]))
    return active, dismissed


def sec_findings(m, ai):
    active, dismissed = merged_findings(m, ai)
    out = ["## 6. Possible defects & areas to consider fixing", "",
           "Rule-based checks run on every formula, plus Microsoft's App Checker results stored in the .msapp"
           + (", plus an AI review (marked *AI review*)" if ai.get("additional_findings") or ai.get("finding_reviews") else "")
           + ". Each item says where it occurs and what to do; use it as a maintenance backlog and as a list of "
             "patterns not to carry into a rebuild.", ""]
    out += [table(["ID", "Severity", "Category", "Finding", "Places", "Source"],
                  [(f["id"], f["severity"], f["category"], f["title"], f["where_count"],
                    {"rule": "rule", "app-checker": "App Checker", "ai-review": "AI review"}.get(f["source"], f["source"]))
                   for f in active]), ""]
    for f in active:
        out += [f"### {f['id']} — {f['title']}", "", f"**Severity:** {f['severity']} · **Category:** {f['category']}"
                + (f" · **Reviewed:** {f['verdict']}" if f.get("verdict") else ""), ""]
        if f.get("review_note"):
            out += [AI_TAG, "> " + f["review_note"], ""]
        if f["evidence"]:
            out += [f"**Evidence:** `{f['evidence']}`" if len(f["evidence"]) < 200 and "`" not in f["evidence"] else f"**Evidence:** {f['evidence']}", ""]
        if f["where"]:
            more = f" … and {f['where_count'] - len(f['where'])} more" if f["where_count"] > len(f["where"]) else ""
            out += ["**Where:** " + "; ".join(f"`{w}`" for w in f["where"][:12]) + (more or (f" … and {len(f['where']) - 12} more" if len(f['where']) > 12 else "")), ""]
        out += [f"**Recommendation:** {f['recommendation']}", ""]
    if dismissed:
        out += ["### Dismissed on review", "", table(["ID", "Finding", "Why dismissed"],
                                                     [(f["id"], f["title"], f.get("review_note", "")) for f in dismissed]), ""]
    return "\n".join(out)


def sec_rebuild(m, ai):
    a = m["analysis"]
    rb = ai.get("rebuild") or {}
    out = ["## 7. Rebuild guide (for a code app)", "",
           "What a replacement must do, derived from the app. Hand this section, plus the data model and screens, "
           "to an AI coding agent.", ""]
    if rb.get("overview"):
        out += [AI_TAG, ">", "> " + rb["overview"].replace("\n", "\n> "), ""]
    if rb.get("suggested_architecture"):
        out += ["**Suggested architecture** (AI interpretation): " + rb["suggested_architecture"], ""]
    out += ["### Entities (facts)", ""]
    for d in m["data_sources"]:
        if d.get("columns") and not d["is_sample"]:
            used = set(a["data_usage"].get(d["name"], {}).get("columns_used", []))
            fields = [f"{c['name']}: {c['type']}{' (required)' if c['required'] else ''}" for c in d["columns"]
                      if not c["system"] or c["name"] in used]
            out += [f"- **{d.get('display_name') or d['name']}** ({d['kind']}): " + "; ".join(fields)]
    out.append("")
    if rb.get("entities"):
        out += ["### Entity notes", "", AI_TAG, ""]
        for e in rb["entities"]:
            out += [f"- **{e.get('name', '')}**: {e.get('notes', '')}" + (f" Fields: {', '.join(e['fields'])}" if e.get("fields") else "")]
        out.append("")
    if rb.get("features"):
        out += ["### Features to rebuild", "", AI_TAG, "",
                table(["Feature", "Behaviour", "Source in app"], [(x.get("name", ""), x.get("behaviour", ""), x.get("source", "")) for x in rb["features"]]), ""]
    if rb.get("business_rules"):
        out += ["### Business rules & validation", "", AI_TAG, "", bullets(rb["business_rules"]), ""]
    active, _ = merged_findings(m, ai)
    avoid = list(rb.get("avoid") or []) + [f"{f['title']} — {f['recommendation']}" for f in active if f["severity"] in ("High", "Medium")]
    out += ["### Patterns not to carry over", "", bullets(avoid), ""]
    return "\n".join(out)


def sec_maintainer(m, ai):
    out = ["## 8. Notes for maintainers", ""]
    if ai.get("maintainer_notes"):
        out += [AI_TAG, "", bullets(ai["maintainer_notes"]), ""]
    if ai.get("open_questions"):
        out += ["### Open questions", "", bullets(ai["open_questions"]), ""]
    if ai.get("glossary"):
        out += ["### Glossary", "", table(["Term", "Meaning"], sorted(ai["glossary"].items())), ""]
    if len(out) == 2:
        out += ["_No AI interpretation yet. Run the interpretation step to add maintainer notes._", ""]
    return "\n".join(out)


def sec_provenance(m, ai):
    s = m["stats"]
    return "\n".join([
        "## Appendix: how this document was produced", "",
        f"- Source: `{m['source']['file']}`, sha256 `{m['source']['sha256']}`",
        f"- Format read: {m['source']['format']} (msapp structure {m['app_info']['msapp_structure_version']})",
        f"- Cleaning: {s['rules_total']} property formulas in the app; {s['rules_kept']} kept in the clean source "
        f"({s['rules_total'] - s['rules_kept']} dropped as layout, static styling, defaults or empty). "
        f"Raw size {s['raw_kb']} KB → clean source {s['clean_kb']} KB.",
        "- Sections without the *AI interpretation* marker are produced deterministically by scripts: the same .msapp "
        "always gives the same text. Marked sections were written by an AI from the extracted chunks.",
        f"- Generator: msapp-documenter {m['generator_version']}" + (" + AI interpretation" if ai else ""), ""])


SECTIONS = [("01-overview", sec_overview), ("02-architecture", sec_architecture), ("03-screens", sec_screens),
            ("04-data-model", sec_data), ("05-integrations", sec_integrations), ("06-findings", sec_findings),
            ("07-rebuild-guide", sec_rebuild), ("08-maintainer-notes", sec_maintainer), ("99-provenance", sec_provenance)]


def render_markdown(m, ai):
    head = [f"# {m['app_info']['name']} — app documentation", "",
            f"_Generated from `{m['source']['file']}` (last saved {m['app_info']['saved_utc']} UTC)._", "",
            "**Contents:** 1 Overview · 2 Architecture · 3 Screens · 4 Data model · 5 Flows & integrations · "
            "6 Possible defects · 7 Rebuild guide · 8 Maintainer notes", ""]
    return "\n".join(head) + "\n" + "\n".join(fn(m, ai) for _, fn in SECTIONS)


def write_ai_context(m, ai, out_dir):
    """Small self-contained chunks with front matter, plus an index, for feeding to an AI."""
    d = os.path.join(out_dir, "ai_context")
    os.makedirs(os.path.join(d, "screens"), exist_ok=True)
    for name in os.listdir(os.path.join(d, "screens")):
        os.remove(os.path.join(d, "screens", name))
    app = m["app_info"]["name"]
    index = []

    def fm(chunk_id, kind, title, extra=None):
        lines = ["---", f"app: {json.dumps(app)}", f"chunk: {chunk_id}", f"kind: {kind}", f"title: {json.dumps(title)}",
                 f"source_sha256: {m['source']['sha256'][:16]}"]
        for k, v in (extra or {}).items():
            lines.append(f"{k}: {json.dumps(v)}")
        return "\n".join(lines + ["---", ""])

    def put(rel, chunk_id, kind, title, body, summary, extra=None):
        with open(os.path.join(d, rel), "w", encoding="utf-8") as f:
            f.write(fm(chunk_id, kind, title, extra) + body.rstrip() + "\n")
        index.append((rel, kind, title, summary))

    put("01-overview.md", "overview", "overview", "App overview", sec_overview(m, ai) + "\n" + sec_architecture(m, ai),
        "App properties, counts, architecture and navigation diagrams")
    put("02-data-model.md", "data-model", "data-model", "Data model", sec_data(m, ai), "Lists/tables, columns, variables, collections")
    put("03-integrations.md", "integrations", "integrations", "Flows & integrations", sec_integrations(m, ai), "Connectors, flows, operations")
    put("04-findings.md", "findings", "findings", "Possible defects", sec_findings(m, ai), "Rule-based + App Checker findings")
    put("05-rebuild-guide.md", "rebuild", "rebuild-guide", "Rebuild guide", sec_rebuild(m, ai), "Entities, features, patterns to avoid")
    for i, s in enumerate(m["analysis"]["screens"], 1):
        src_path = os.path.join(out_dir, "source", "screens", s["file"])
        with open(src_path, encoding="utf-8") as f:
            yaml_src = f.read()
        body = sec_screen(m, s, ai, i, for_chunk=True) + "\n**Clean control tree**\n\n```yaml\n" + yaml_src.rstrip() + "\n```\n"
        put(f"screens/{s['file'][:-5]}.md", f"screen/{slug(s['name'])}", "screen", s["name"], body,
            f"{s['control_count']} controls; data: {', '.join(s['data_sources'] + s['flows']) or '-'}",
            {"navigates_to": s["navigates_to"], "data_sources": s["data_sources"]})
    with open(os.path.join(out_dir, "source", "App.yaml"), encoding="utf-8") as f:
        put("06-app-logic.md", "app-logic", "app", "App-level properties (OnStart, Formulas, StartScreen)",
            "```yaml\n" + f.read().rstrip() + "\n```", "App.OnStart / Formulas / StartScreen")
    lines = [f"# AI context pack: {app}", "",
             "Self-contained chunks describing a Power Apps canvas app. Each chunk has front matter. "
             "`app_model.json` holds everything in structured form. Text marked *AI interpretation* is inferred; "
             "everything else is extracted deterministically from the .msapp.", "",
             table(["File", "Kind", "Title", "Summary"], [(f"`{r}`", k, t, s) for r, k, t, s in index]), ""]
    with open(os.path.join(d, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
