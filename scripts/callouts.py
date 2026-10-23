"""Numbered callouts for wireframes: pick the controls that matter and describe them from their formulas.

A callout is any control a maintainer would ask "what does this do?" about: buttons and other
clickable controls, inputs, choice controls, galleries/forms, and text computed from data.
Descriptions are deterministic plain English built from the control's own formulas; the AI layer
adds context separately (interpretation.json "controls").
"""
import re

import fx
from msapp_analyze import resolve_active_screen_switch
from msapp_model import walk, is_behavior

INTERACTIVE = {"button", "input", "dropdown", "radio", "checkbox", "toggle", "richtext", "gallery", "form"}
KNOWN = ["Navigate", "Back", "Patch", "SubmitForm", "ResetForm", "NewForm", "EditForm", "ViewForm", "Remove",
         "RemoveIf", "UpdateIf", "ClearCollect", "Collect", "Clear", "Set", "UpdateContext", "Reset", "Exit",
         "Launch", "Notify", "Refresh", "Download", "Print", "Copy", "SaveData", "LoadData", "Select"]
MAX_CALLOUTS = 20


def _rules(node):
    return {r["property"]: r["formula"] for r in node["rules"]}


def _record_keys(arg):
    a = arg.strip()
    if not a.startswith("{"):
        return []
    return [fx.unquote_ident(kv.split(":", 1)[0].strip()) for kv in fx.split_top(a[1:-1]) if ":" in kv]


def describe_actions(formula, screen, screens, flows):
    """Plain-English sequence of what a behaviour formula does, in order of appearance."""
    steps, resets = [], []
    code = fx.strip_strings(formula)
    for fn, args, start, _ in sorted(fx.calls(formula, KNOWN), key=lambda c: c[2]):
        a0 = fx.unquote_ident(args[0]) if args else ""
        if fn == "Navigate" and args:
            targets, _ = resolve_active_screen_switch(args[0], screen, set(screens))
            if targets is None:
                targets = [s for s in screens if fx.references(args[0], s)]
            if targets:
                steps.append("go to " + " / ".join(targets))
            elif targets == []:
                steps.append("no navigation from this screen")
        elif fn == "Back":
            steps.append("go back")
        elif fn == "Patch" and args:
            keys = _record_keys(args[-1]) if len(args) > 2 else []
            verb = "create a new" if len(args) > 1 and "Defaults(" in args[1] else "update a"
            steps.append(f"{verb} {a0} record" + (f" ({', '.join(keys[:6])}{'…' if len(keys) > 6 else ''})" if keys else ""))
        elif fn == "SubmitForm":
            steps.append(f"submit form {a0}")
        elif fn in ("ResetForm", "NewForm", "EditForm", "ViewForm"):
            steps.append(f"{fn[:-4].lower()} form {a0}")
        elif fn in ("Remove", "RemoveIf"):
            steps.append(f"delete from {a0}")
        elif fn == "UpdateIf":
            steps.append(f"bulk-update {a0}")
        elif fn == "ClearCollect":
            keys = sorted({k for x in args[1:] for k in re.findall(r"[{,]\s*(" + fx.IDENT + r")\s*:", fx.strip_strings(x))})
            steps.append(f"fill collection {a0}" + (f" ({', '.join(keys)})" if keys else ""))
        elif fn == "Collect":
            steps.append(f"add to {a0}")
        elif fn == "Clear":
            steps.append(f"empty {a0}")
        elif fn == "Set":
            steps.append(f"set {a0}")
        elif fn == "UpdateContext":
            steps.append("set " + ", ".join(_record_keys(args[0])) if args else "set screen variables")
        elif fn == "Reset":
            if not resets:
                steps.append("__RESETS__")
            resets.append(a0)
        elif fn == "Exit":
            steps.append("close the app")
        elif fn == "Launch":
            steps.append("open " + _clip(args[0], 40) if args else "open a link")
        elif fn == "Notify":
            lits = fx.string_literals(args[0]) if args else []
            steps.append("show message" + (f" “{lits[0][:40]}”" if lits else ""))
        elif fn == "Refresh":
            steps.append(f"reload {a0}")
        elif fn in ("SaveData", "LoadData"):
            steps.append(f"{fn[:-4].lower()} offline data {a0}")
        elif fn in ("Download", "Print", "Copy"):
            steps.append(fn.lower())
    for f in flows:
        if re.search(r"(?<![\w.])'?" + re.escape(f) + r"'?\.Run\s*\(", code):
            steps.append(f"run flow {f}")
    if resets:
        steps[steps.index("__RESETS__")] = f"clear {len(resets)} inputs" if len(resets) > 1 else f"clear {resets[0]}"
    if re.search(r"(?<![\w.])(IfError|IsError|Errors)\s*\(", code):
        steps.append("(handles errors)")
    out = " → ".join(s for s in steps if s)
    # Only call it conditional when an If/Switch actually wraps an action, not when it computes a value.
    action_re = re.compile(r"(?<![\w.])(" + "|".join(k for k in KNOWN if k != "Select") + r")\s*\(|\.Run\s*\(")
    if out and any(action_re.search(fx.strip_strings(",".join(a[1:]))) for _, a, _, _ in fx.calls(formula, ["If", "Switch", "IfError"])):
        out = "conditionally: " + out
    return out[:1].upper() + out[1:] if out else ""


def _clip(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def build(screen_node, wireframe, formulas, screens, data_sources, collections):
    """Assign callout numbers to wireframe boxes (in reading order) and describe each control."""
    nodes = {n["name"]: n for n in walk(screen_node)}
    parents = {}
    for n in walk(screen_node):
        for c in n["children"]:
            parents[c["name"]] = n
    flows = [d["name"] for d in data_sources if d["kind"] == "Power Automate flow"]
    ds_by = {d["name"]: d for d in data_sources}
    coll_by = {c["name"]: c for c in collections}

    def in_gallery(name):
        p = parents.get(name)
        while p is not None:
            if p["template"] == "gallery":
                return True
            p = parents.get(p["name"])
        return False

    def used_in(name):
        out = []
        for f in formulas:
            if f["control"] == name or f["property"] in ("X", "Y", "Width", "Height"):
                continue
            code = f["code"]
            for _, args, st, en in fx.calls(f["formula"], "Reset"):  # Reset() clears an input, it doesn't read it
                code = code[:st] + " " * (en - st + 1) + code[en + 1:]
            if fx.references(f["formula"], name, code):
                own = f["owner"] + " › " if f["owner"] != screen_node["name"] else ""
                out.append(f"{own}{f['control']}.{f['property']}")
        return sorted(set(out))

    picks = []
    for b in wireframe["boxes"]:
        node = nodes.get(b["name"])
        if node is None:
            continue
        rules = _rules(node)
        actions = {p: f for p, f in rules.items() if is_behavior(p) and f.lower() not in ("false", "true", "")
                   and f.replace(" ", "") != "Select(Parent)"}
        computed_text = b["kind"] in ("label", "other") and b["text"] == "ƒx"
        if in_gallery(b["name"]) and not actions:
            continue
        if b["kind"] in INTERACTIVE or actions or computed_text:
            picks.append((b, node, rules, actions))
    picks.sort(key=lambda p: (p[0]["y"] // 24, p[0]["x"]))
    picks = picks[:MAX_CALLOUTS]

    callouts = []
    for n, (b, node, rules, actions) in enumerate(picks, 1):
        b["callout"] = n
        parts = []
        k = b["kind"]
        if k == "gallery":
            items = rules.get("Items", "")
            src = next((x for x in list(ds_by) + list(coll_by) if fx.references(items, x)), None)
            shown = sorted({m for c in walk(node) for f in c["rules"]
                            for m in re.findall(r"ThisItem\.('?[^'.\s,)]+'?|\w+)", f["formula"])})
            shown = [fx.unquote_ident(s) for s in shown]
            desc = f"Lists {src if src else _clip(items, 50)}"
            if src in coll_by and coll_by[src]["inferred_fields"]:
                desc += f" (collection: {', '.join(coll_by[src]['inferred_fields'])})"
            elif src in ds_by:
                desc += f" ({ds_by[src]['kind']})"
            if shown:
                desc += f"; each row shows {', '.join(shown[:6])}"
            parts.append(desc)
        elif k in ("dropdown", "radio"):
            items = rules.get("Items", "")
            lits = fx.string_literals(items)
            if lits and items.strip().startswith("["):
                parts.append("Options: " + ", ".join(lits[:8]))
            elif items:
                parts.append("Options from " + _clip(items, 60))
            multi = any(rules.get(p, "").lower() == "true" for p in ("AllowMultiSelect", "SelectMultiple"))
            if multi:
                parts.append("multi-select")
            if rules.get("DefaultSelectedItems") or rules.get("Default"):
                parts.append("default " + _clip(rules.get("DefaultSelectedItems") or rules.get("Default"), 40))
        elif k in ("input", "richtext", "checkbox", "toggle"):
            label = {"input": "Text input", "richtext": "Rich text (HTML)", "checkbox": "Check box", "toggle": "Toggle"}[k]
            if "Multiline" in rules.get("Mode", "") or "MultiLine" in rules.get("Mode", ""):
                label += ", multi-line"
            if rules.get("MaxLength") and fx.is_static(rules["MaxLength"]):
                label += f", max {rules['MaxLength']} chars"
            default = rules.get("Default") or rules.get("Value") or rules.get("DefaultText")
            if default and default not in ('""', ""):
                label += f", default {_clip(default, 40)}"
            if rules.get("HintText") or rules.get("Placeholder"):
                label += f", hint {_clip(rules.get('HintText') or rules.get('Placeholder'), 40)}"
            parts.append(label)
        elif k in ("label", "other") and b["text"] == "ƒx":
            parts.append("Shows computed text: " + _clip(rules.get("Text", ""), 90))
        for p, f in sorted(actions.items()):
            d = describe_actions(f, screen_node["name"], screens, flows)
            if d:
                parts.append(f"{p}: {d}" if p != "OnSelect" or k not in ("button",) else d)
        for p, verb in (("DisplayMode", "Enabled/disabled by"), ("Visible", "Visible when")):
            f = rules.get(p)
            if f and not fx.is_static(f) and p == "DisplayMode":
                parts.append(f"{verb} {_clip(f, 60)}")
        if k in ("input", "richtext", "dropdown", "radio", "checkbox", "toggle"):
            u = used_in(b["name"])
            parts.append("Value read by " + ("; ".join(u[:3]) + (f" (+{len(u) - 3})" if len(u) > 3 else "") if u else "nothing (unused input)"))
        callouts.append({"n": n, "name": b["name"], "type": b["type"], "kind": k, "does": ". ".join(parts) or "-"})
    return callouts
