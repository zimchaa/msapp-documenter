"""Deterministic analysis of a loaded app model: symbols, usage, navigation, data model, findings.

Every result here is derived by rules from the app's own formulas and metadata, so
the same .msapp always produces the same analysis. Interpretation (what the app is
*for*, whether a finding really matters) is left to the AI layer.
"""
import re
from collections import defaultdict

import fx
from msapp_model import walk, is_behavior, LAYOUT_PROPS

SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Info": 3}

INPUT_TYPES = re.compile(r"(?i)text|combo|drop|date|radio|toggle|check|slider|rating|pen|attach|picker|"
                         r"upload|barcode|camera|addmedia|richtext|number|search")
DATA_WRITE_FUNCS = ["Patch", "Remove", "RemoveIf", "UpdateIf", "Collect", "ClearCollect", "Clear", "Update"]
DEFAULT_NAME = re.compile(
    r"(?i)^(button|label|textinput|text|gallery|icon|image|rectangle|circle|container|form|editform|"
    r"formviewer|datacard|datacardvalue|datacardkey|errormessage|starvisible|dropdown|combobox|toggle|"
    r"checkbox|datepicker|timer|htmltext|screen|group|radio|slider|rating|textbox|textcanvas|"
    r"buttoncanvas|richtexteditor|pdfviewer|separator|horizontalcontainer|verticalcontainer|"
    r"tabletter|table|header|pen|attachments|addmedia|uploadedimage|camera)\s?\d+(_\d+)*$")
NON_DELEGABLE_IN_QUERY = re.compile(r"(?<![\w.])(Len|Lower|Upper|Trim|TrimEnds|Left|Right|Mid|Text|Value|Year|Month|"
                                    r"Day|Hour|DateValue|DateDiff|DateAdd|Find|Substitute|IsMatch|Concatenate|"
                                    r"CountIf|First|Last)\s*\(|\s(in|exactin)\s")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL = re.compile(r"https?://[^\s\"']+")
GUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

APP_CHECKER_ADVICE = {
    "acc-AccessibleLabelNeeded": ("Accessibility: controls missing an AccessibleLabel",
                                  "Give interactive controls and images an AccessibleLabel so screen readers can announce them."),
    "acc-TabIndexShouldBeDefinedForInteractiveControl": ("Accessibility: interactive controls without TabIndex",
                                                         "Set TabIndex (0 for focusable) so keyboard users can reach every control in a sensible order."),
    "acc-FocusBorderShouldBeVisible": ("Accessibility: focus border not visible",
                                       "Make FocusedBorderThickness > 0 so keyboard focus is visible."),
    "acc-ColorContrast": ("Accessibility: low colour contrast", "Raise text/background contrast to at least 4.5:1."),
    "app-UnusedMediaResources": ("Unused media files in the app",
                                 "Delete unused images/media to reduce app size and load time."),
    "app-UnusedVariables": ("Unused variables", "Remove variables that are never read."),
    "perf-ReferenceToUnusedVariable": ("Reference to unused variable", "Remove or wire up the variable."),
}


def _formulas(model):
    out = []
    owners = [("App", model["app_node"])] + [(s["name"], s) for s in model["screens"]] + \
             [(c["name"], c) for c in model["components"]]
    for owner, root in owners:
        for node in walk(root):
            for r in node["rules"]:
                if not r["formula"]:
                    continue
                out.append({"owner": owner, "control": node["name"], "type": node["type"], "template": node["template"],
                            "property": r["property"], "formula": r["formula"], "code": fx.strip_strings(r["formula"]),
                            "behavior": is_behavior(r["property"])})
    return out


def _where(f):
    return f"{f['owner']} › {f['control']}.{f['property']}" if f["control"] != f["owner"] else f"{f['owner']}.{f['property']}"


def _short(text, n=160):
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def resolve_active_screen_switch(arg, current_screen, screen_names):
    """Navigate(Switch(App.ActiveScreen.Name, "A", 'B', ...)) evaluated for the screen that owns the formula."""
    for name, args, _, _ in fx.calls(arg, "Switch"):
        if not args or not re.fullmatch(r"App\.ActiveScreen(\.Name)?", args[0].replace(" ", "")):
            continue
        pairs = args[1:]
        for i in range(0, len(pairs) - 1, 2):
            lits = fx.string_literals(pairs[i])
            if lits and lits[0] == current_screen:
                return [fx.unquote_ident(pairs[i + 1])], True
        if len(pairs) % 2 == 1:
            return [fx.unquote_ident(pairs[-1])], True
        return [], True
    return None, False


def analyze(model):
    formulas = _formulas(model)
    screens = [s["name"] for s in model["screens"]]
    screen_set = set(screens)
    controls = {}
    for owner in [model["app_node"]] + model["screens"] + model["components"]:
        for n in walk(owner):
            controls.setdefault(n["name"], {"owner": owner["name"], "type": n["type"], "template": n["template"],
                                            "auto": n["is_auto_generated"], "node": n})

    ds_by_name = {d["name"]: d for d in model["data_sources"]}
    tables = [d for d in model["data_sources"] if d["kind"] not in ("Collection", "Connector", "Power Automate flow")]
    flows = [d for d in model["data_sources"] if d["kind"] == "Power Automate flow"]
    connectors = [d for d in model["data_sources"] if d["kind"] == "Connector"]

    # ---------------------------------------------------------------- variables & collections
    gvars, cvars, colls, named = defaultdict(list), defaultdict(list), defaultdict(list), {}
    for d in model["data_sources"]:
        if d["kind"] == "Collection":
            colls.setdefault(d["name"], [])
    for f in formulas:
        if f["owner"] == "App" and f["property"] == "Formulas":
            for part in fx.split_top(f["formula"], ";"):
                m = re.match(r"\s*(" + fx.IDENT + r"|'[^']+')\s*=", part)
                if m:
                    named[fx.unquote_ident(m.group(1))] = fx.normalize(part.split("=", 1)[1])
        for _, args, _, _ in fx.calls(f["formula"], "Set"):
            if args:
                gvars[fx.unquote_ident(args[0])].append({"where": _where(f), "value": _short(args[1] if len(args) > 1 else "")})
        for _, args, _, _ in fx.calls(f["formula"], "UpdateContext"):
            if args and args[0].startswith("{"):
                for kv in fx.split_top(args[0][1:-1]):
                    k = kv.split(":", 1)[0].strip()
                    if k:
                        cvars[fx.unquote_ident(k)].append({"where": _where(f), "scope": f["owner"]})
        for _, args, _, _ in fx.calls(f["formula"], "Navigate"):
            if len(args) >= 3 and args[-1].startswith("{"):
                for kv in fx.split_top(args[-1][1:-1]):
                    k = kv.split(":", 1)[0].strip()
                    if k:
                        cvars[fx.unquote_ident(k)].append({"where": _where(f), "scope": "passed via Navigate"})
        for fn, args, _, _ in fx.calls(f["formula"], ["ClearCollect", "Collect"]):
            if args:
                target = fx.unquote_ident(args[0])
                if target not in ds_by_name or ds_by_name[target]["kind"] == "Collection":
                    fields = sorted(set(re.findall(r"[{,]\s*(" + fx.IDENT + r")\s*:", fx.strip_strings(",".join(args[1:])))))
                    colls[target].append({"where": _where(f), "op": fn, "inferred_fields": fields})

    def usage(name, write_funcs=()):
        pat = fx.ident_pattern(name)
        reads, writes = [], []
        for f in formulas:
            n = len(pat.findall(f["code"]))
            if not n:
                continue
            w = 0
            for _, args, _, _ in fx.calls(f["formula"], list(write_funcs)):
                if args and fx.unquote_ident(args[0]) == name:
                    w += 1
            if w:
                writes.append(_where(f))
            if n > w:
                reads.append(_where(f))
        return sorted(set(reads)), sorted(set(writes))

    variables = []
    for name in sorted(gvars):
        r, w = usage(name, ["Set"])
        variables.append({"name": name, "scope": "global", "set_in": sorted({x["where"] for x in gvars[name]}),
                          "read_in": r, "example_value": gvars[name][0]["value"]})
    for name in sorted(cvars):
        r, w = usage(name, [])
        sets = sorted({x["where"] for x in cvars[name]})
        variables.append({"name": name, "scope": "context (screen)", "set_in": sets,
                          "read_in": [x for x in r if x not in sets], "example_value": ""})
    for name in sorted(named):
        r, _ = usage(name)
        variables.append({"name": name, "scope": "named formula", "set_in": ["App.Formulas"], "read_in": r,
                          "example_value": _short(named[name])})

    collections = []
    for name in sorted(colls):
        r, w = usage(name, ["Collect", "ClearCollect", "Clear", "Remove", "RemoveIf", "Patch", "UpdateIf"])
        fields = sorted({x for c in colls[name] for x in c["inferred_fields"]})
        collections.append({"name": name, "written_in": w, "read_in": r, "inferred_fields": fields})

    # ---------------------------------------------------------------- data sources, flows, connectors
    def ds_usage(d):
        r, w = usage(d["name"], DATA_WRITE_FUNCS)
        # SubmitForm(form) writes to the form's DataSource
        for f in formulas:
            for _, args, _, _ in fx.calls(f["formula"], "SubmitForm"):
                if args:
                    form = controls.get(fx.unquote_ident(args[0]))
                    if form and any(rr["property"] == "DataSource" and fx.references(rr["formula"], d["name"])
                                    for rr in form["node"]["rules"]):
                        w = sorted(set(w) | {_where(f)})
        return r, w

    data_usage = {}
    for d in model["data_sources"]:
        if d["kind"] == "Collection":
            continue
        r, w = ds_usage(d)
        ops = set()
        if d["kind"] in ("Connector", "Power Automate flow"):
            pat = re.compile(r"(?:'" + re.escape(d["name"]) + r"'|(?<![\w.])" + re.escape(d["name"]) + r")\.(\w+)\s*\(")
            for f in formulas:
                ops.update(pat.findall(f["code"]))
        used_cols = []
        for c in d.get("columns", []):
            # Display names are only matched when they need quoting ('Due Date'); single words like "Name"
            # collide with record fields of other tables. System columns ({Name}, {Link}) match exactly.
            names = {c["name"]}
            if not c["system"] and " " in (c.get("display_name") or ""):
                names.add(c["display_name"])
            if any(fx.references(f["formula"], nm, f["code"]) for f in formulas for nm in names if nm):
                used_cols.append(c["name"])
        data_usage[d["name"]] = {"read_in": r, "written_in": w, "operations": sorted(ops), "columns_used": sorted(used_cols)}

    # ---------------------------------------------------------------- navigation
    nav = []
    for f in formulas:
        for fn, args, _, _ in fx.calls(f["formula"], ["Navigate", "Back"]):
            if fn == "Back":
                nav.append({"from": f["owner"], "to": "(previous screen)", "via": _where(f), "resolved": True})
                continue
            if not args:
                continue
            targets, resolved = resolve_active_screen_switch(args[0], f["owner"], screen_set)
            if targets is None:
                code = fx.strip_strings(args[0])
                targets = [s for s in screens if fx.references(args[0], s, code)]
                resolved = len(targets) == 1
            for t in targets:
                nav.append({"from": f["owner"], "to": t, "via": _where(f), "resolved": resolved})
    nav_edges = {}
    for e in nav:
        key = (e["from"], e["to"])
        cur = nav_edges.setdefault(key, {"from": e["from"], "to": e["to"], "via": [], "conditional": False})
        cur["via"].append(e["via"])
        cur["conditional"] = cur["conditional"] or not e["resolved"]
    navigation = sorted(nav_edges.values(), key=lambda e: (screens.index(e["from"]) if e["from"] in screen_set else -1,
                                                           e["to"]))
    start = next((f["formula"] for f in formulas if f["owner"] == "App" and f["property"] == "StartScreen"), "")
    start_screen = fx.unquote_ident(start) if fx.unquote_ident(start) in screen_set else (screens[0] if screens else "")

    launches = sorted({_short(a[0]) for f in formulas for _, a, _, _ in fx.calls(f["formula"], "Launch") if a})

    # ---------------------------------------------------------------- per-screen summary
    screen_summ = []
    for s in model["screens"]:
        nodes = list(walk(s))
        fs = [f for f in formulas if f["owner"] == s["name"]]
        actions = [{"where": _where(f), "control": f["control"], "property": f["property"], "formula": f["formula"]}
                   for f in fs if f["behavior"] and f["formula"].lower() not in ("false", "true", "")
                   and f["formula"].replace(" ", "") not in ("Select(Parent)",)]
        inputs = [{"name": n["name"], "type": n["type"]} for n in nodes
                  if INPUT_TYPES.search(n["template"]) and n["template"] not in ("label",) and "Label" not in n["template"]
                  and n["template"] not in ("htmlViewer", "pcfDataField", "typedDataCard")]
        bindings = [{"where": _where(f), "control": f["control"], "property": f["property"], "formula": f["formula"]} for f in fs
                    if f["property"] in ("Items", "DataSource", "Item", "Default", "DefaultSelectedItems", "Value")
                    and not fx.is_static(f["formula"])]
        def refs(names):
            return sorted({n for n in names for f in fs if fx.references(f["formula"], n, f["code"])})
        screen_summ.append({
            "name": s["name"],
            "control_count": len(nodes) - 1,
            "control_types": sorted({n["type"] for n in nodes[1:]}),
            "inputs": inputs,
            "actions": actions,
            "bindings": bindings,
            "data_sources": refs([d["name"] for d in tables]),
            "data_written": sorted({d for d, u in data_usage.items() if any(w.startswith(s["name"] + " ›") or w.startswith(s["name"] + ".") for w in u["written_in"])}),
            "flows": refs([d["name"] for d in flows]),
            "connectors": refs([d["name"] for d in connectors]),
            "variables_set": sorted({v["name"] for v in variables if any(x.startswith(s["name"]) for x in v["set_in"])}),
            "variables_read": sorted({v["name"] for v in variables if any(x.startswith(s["name"]) for x in v["read_in"])}),
            "collections": refs(list(colls)),
            "navigates_to": sorted({e["to"] for e in navigation if e["from"] == s["name"] and e["to"] != s["name"]}),
            "navigated_from": sorted({e["from"] for e in navigation if e["to"] == s["name"] and e["from"] != s["name"]}),
        })

    analysis = {
        "start_screen": start_screen,
        "screens": screen_summ,
        "navigation": navigation,
        "variables": variables,
        "collections": collections,
        "data_usage": data_usage,
        "launch_targets": launches,
        "formula_count": len(formulas),
    }
    analysis["findings"] = findings(model, analysis, formulas, controls, ds_by_name, screens)
    return analysis


# ============================================================================= findings

def findings(model, a, formulas, controls, ds_by_name, screens):
    out = []

    def add(rule, title, severity, category, where, evidence, recommendation, source="rule"):
        out.append({"rule": rule, "title": title, "severity": severity, "category": category,
                    "where": sorted(set(where))[:25], "where_count": len(set(where)), "evidence": evidence,
                    "recommendation": recommendation, "source": source})

    connected = [d for d in model["data_sources"] if d["kind"] in ("SharePoint list", "Connected table", "Dataverse table")]
    row_limit = model["app_info"].get("row_limit") or 500

    # 1. writes without error handling
    hits = []
    for f in formulas:
        if not f["behavior"]:
            continue
        if re.search(r"(?<![\w.])(Patch|Remove|RemoveIf|UpdateIf|SubmitForm)\s*\(|\.Run\s*\(", f["code"]) and \
                not re.search(r"(?<![\w.])(IfError|IsError|Errors|IsEmpty\s*\(\s*Errors)\s*\(", f["code"]):
            if "SubmitForm" in f["code"] and not re.search(r"(?<![\w.])(Patch|Remove|RemoveIf|UpdateIf)\s*\(|\.Run\s*\(", f["code"]):
                continue  # forms report failures through OnFailure; checked separately below
            hits.append(f)
    if hits:
        add("no-error-handling", "Data writes or flow calls without error handling", "Medium", "Reliability",
            [_where(f) for f in hits], _short(hits[0]["formula"], 220),
            "Wrap Patch/Remove/flow .Run calls in IfError(...) (or check Errors(DataSource)) and tell the user "
            "what failed; otherwise a failed save looks like success and data is silently lost.")
    for name, c in controls.items():
        if c["template"] in ("form",):
            rules = {r["property"]: r["formula"] for r in c["node"]["rules"]}
            if not rules.get("OnFailure") or rules.get("OnFailure", "").lower() in ("false", ""):
                add("form-no-onfailure", f"Form '{name}' has no OnFailure handler", "Medium", "Reliability",
                    [f"{c['owner']} › {name}.OnFailure"], "OnFailure is empty",
                    "Add an OnFailure that notifies the user (Notify(...)) and keeps their input.")

    # 2. ForAll with Patch inside
    fa = [f for f in formulas for _, args, _, _ in fx.calls(f["formula"], "ForAll")
          if re.search(r"(?<![\w.])(Patch|Collect|Remove)\s*\(", fx.strip_strings(",".join(args[1:])))]
    if fa:
        add("forall-patch", "Row-by-row writes inside ForAll", "Low", "Performance", [_where(f) for f in fa],
            _short(fa[0]["formula"], 200),
            "Each iteration is a separate network call. Prefer Patch(DataSource, table) with a table of records, "
            "and handle partial failure explicitly.")

    # 3. delegation risks
    for d in connected:
        nm = d["name"]
        risky = []
        for f in formulas:
            for fn, args, _, _ in fx.calls(f["formula"], ["Filter", "Search", "LookUp", "Sort", "SortByColumns", "AddColumns", "GroupBy", "Distinct", "CountRows", "Sum", "Average", "Min", "Max", "CountIf"]):
                if not args or fx.unquote_ident(args[0]) != nm:
                    continue
                cond = fx.strip_strings(",".join(args[1:]))
                if fn in ("Search", "AddColumns", "GroupBy", "Distinct", "CountRows", "Sum", "Average", "Min", "Max", "CountIf") \
                        and "Dataverse" not in d["kind"]:
                    risky.append((f, f"{fn}() over {nm}"))
                elif NON_DELEGABLE_IN_QUERY.search(" " + cond):
                    risky.append((f, f"{fn}({nm}, …) uses {NON_DELEGABLE_IN_QUERY.search(' ' + cond).group(0).strip()}"))
            for fn, args, _, _ in fx.calls(f["formula"], ["ClearCollect", "Collect"]):
                if len(args) > 1 and fx.unquote_ident(args[1]) == nm:
                    risky.append((f, f"{fn} copies the whole of {nm} locally"))
        if risky:
            add("delegation", f"Possible delegation limit on '{nm}'", "Medium", "Data / scale", [_where(f) for f, _ in risky],
                "; ".join(sorted({e for _, e in risky}))[:300],
                f"Non-delegable queries only see the first {row_limit} rows (app setting, max 2000). Results become "
                f"silently incomplete as {d.get('display_name', nm)} grows. Use delegable filters (=, StartsWith, "
                "indexed columns) or move the query server-side.")

    # 4. hard-coded environment values
    hard = defaultdict(list)
    for f in formulas:
        for lit in fx.string_literals(f["formula"]):
            for m in EMAIL.findall(lit):
                hard["email address " + m].append(_where(f))
            for m in URL.findall(lit):
                hard["URL " + m].append(_where(f))
            for m in GUID.findall(lit):
                hard["ID " + m].append(_where(f))
            if "i:0#.f|membership|" in lit:
                hard["SharePoint claims prefix 'i:0#.f|membership|'"].append(_where(f))
    for d in connected:
        if d.get("site"):
            hard["data source site " + d["site"]].append(f"data source {d['name']}")
    if hard:
        where = [w for v in hard.values() for w in v]
        add("hard-coded-values", "Hard-coded environment-specific values", "Low", "Maintainability", where,
            "; ".join(sorted(hard))[:400],
            "Move URLs, IDs, addresses and site/list bindings into environment variables or App.Formulas named "
            "formulas, so the app can move between dev/test/prod without editing formulas.")

    # 5. logic keyed on screen names held as strings
    sn = [f for f in formulas if any(lit in screens for lit in fx.string_literals(f["formula"]))]
    if sn:
        add("screen-name-strings", "Logic depends on screen names as text", "Medium", "Maintainability",
            [_where(f) for f in sn], _short(sn[0]["formula"], 200),
            "Renaming a screen silently breaks these formulas (no compile error). Compare screen objects "
            "(App.ActiveScreen = 'Screen') or drive the flow from a step variable/table instead.")

    # 6. duplicated formulas (one finding summarising every copied block)
    groups = defaultdict(list)
    for f in formulas:
        norm = re.sub(r"\s+", " ", f["formula"])
        if len(norm) >= 60 and f["property"] not in LAYOUT_PROPS and f["property"] != "Size":
            groups[norm].append(f)
    dups = [(norm, fs) for norm, fs in groups.items() if len({(_f["owner"], _f["control"]) for _f in fs}) >= 3]
    if dups:
        dups.sort(key=lambda kv: (-len(kv[1]), kv[0]))
        summary = [f"{fs[0]['control'].split(' - ')[-1]}.{fs[0]['property']} ×{len(fs)}" for _, fs in dups]
        add("duplicated-logic", f"{len(dups)} formulas are copy-pasted across screens", "Low", "Maintainability",
            [_where(f) for _, fs in dups for f in fs], "; ".join(summary)[:300],
            "A fix must be repeated in every copy and copies drift apart. Move shared UI (header, stepper, "
            "nav buttons) into a component and shared logic into App.Formulas named formulas or user-defined functions.")

    # 7. unused things
    for v in a["variables"]:
        if not v["read_in"] and v["scope"] != "named formula":
            add("unused-variable", f"Variable '{v['name']}' is set but never read", "Low", "Maintainability",
                v["set_in"], f"set in {', '.join(v['set_in'][:3])}", "Remove it, or wire it up if something is missing.")
        if v["read_in"] and not v["set_in"]:
            add("unset-variable", f"Variable '{v['name']}' is read but never set", "Medium", "Correctness",
                v["read_in"], "", "It will always be blank. Check for a typo or missing Set().")
    for c in a["collections"]:
        if not c["read_in"]:
            add("unused-collection", f"Collection '{c['name']}' is filled but never read", "Low", "Maintainability",
                c["written_in"] or ["(declared)"], "", "Remove the collection or the code that fills it.")
    for d in model["data_sources"]:
        if d["kind"] == "Collection":
            continue
        u = a["data_usage"].get(d["name"], {})
        if not u.get("read_in") and not u.get("written_in"):
            add("unused-data-source", f"Data source '{d['name']}' is connected but not used", "Low", "Maintainability",
                [f"data source {d['name']}"], f"{d['kind']}{' (sample data)' if d['is_sample'] else ''}",
                "Remove unused connections; they add load time and permission prompts.")
    reachable = {a["start_screen"]} | {e["to"] for e in a["navigation"]}
    for s in screens:
        if s not in reachable:
            add("unreachable-screen", f"Screen '{s}' has no navigation into it", "Medium", "Correctness", [s],
                "No Navigate() targets this screen and it is not the start screen.",
                "Confirm it is reachable (e.g. via a deep link/StartScreen parameter) or remove it.")

    # 8. App.OnStart doing work
    onstart = next((f for f in formulas if f["owner"] == "App" and f["property"] == "OnStart"), None)
    if onstart:
        n_lines = onstart["formula"].count("\n") + 1
        consts = [args[0] for _, args, _, _ in fx.calls(onstart["formula"], "Set") if len(args) > 1 and fx.is_static(args[1])]
        loads = [d["name"] for d in connected if fx.references(onstart["formula"], d["name"], onstart["code"])]
        if loads or n_lines > 40 or consts or fx.calls(onstart["formula"], "ClearCollect"):
            bits = []
            if loads:
                bits.append("loads " + ", ".join(loads))
            if consts:
                bits.append("sets constants " + ", ".join(consts))
            if fx.calls(onstart["formula"], "ClearCollect"):
                bits.append("builds collections")
            bits.append(f"{n_lines} lines")
            add("onstart", "App.OnStart does start-up work", "Low", "Performance", ["App.OnStart"], "; ".join(bits),
                "OnStart blocks the first screen. Use App.Formulas named formulas for constants/static tables "
                "(evaluated lazily) and App.StartScreen instead of Navigate in OnStart.")

    # 9. default control names
    defaults = sorted(n for n, c in controls.items() if DEFAULT_NAME.match(n) and not c["auto"]
                      and c["template"] not in ("galleryTemplate", "appinfo", "pcfDataField"))
    if defaults:
        add("default-names", f"{len(defaults)} controls keep default names", "Low", "Maintainability", defaults,
            ", ".join(defaults[:8]), "Rename to purpose-based names (e.g. btnSubmit, galRequests) so formulas read clearly.")

    # 10. sample / placeholder content
    lorem = [f for f in formulas if "lorem ipsum" in f["formula"].lower()]
    if lorem:
        add("placeholder-content", "Placeholder 'Lorem ipsum' text in the app", "Low", "Quality", [_where(f) for f in lorem],
            "", "Replace or remove placeholder content.")

    # 11. App Checker
    by_rule = defaultdict(list)
    for r in model["app_checker"]:
        by_rule[r["rule"]].append(r)
    for rule, rs in sorted(by_rule.items()):
        title, rec = APP_CHECKER_ADVICE.get(rule, (rs[0]["description"] or rule, "See the Power Apps App Checker for details."))
        lvl = (rs[0]["level"] or "").capitalize()
        sev = {"High": "Medium", "Critical": "High", "Error": "High", "Medium": "Low", "Warning": "Low"}.get(lvl, "Low")
        locs = []
        for r in rs:
            parts = [fx.unquote_ident(x) for x in fx.split_top(r["location"], ".")] or [r["location"]]
            locs.append(f"{parts[0]} › {'.'.join(parts[-2:])}" if len(parts) > 2 else ".".join(parts))
        add(rule, f"{title} ({len(rs)})", sev, "Accessibility" if rule.startswith("acc-") else "App Checker",
            locs, f"Power Apps App Checker rule {rule}, level {rs[0]['level'] or 'n/a'}", rec, source="app-checker")

    out.sort(key=lambda x: (SEVERITY_ORDER.get(x["severity"], 9), x["source"] != "rule", x["category"], x["title"]))
    for i, f in enumerate(out, 1):
        f["id"] = f"F{i:02d}"
    return out
