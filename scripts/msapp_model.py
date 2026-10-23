"""Read a .msapp (or a solution .zip containing one) into a clean, deterministic app model.

The .msapp is a zip. Classic exports hold one JSON control tree per screen in
Controls/*.json (no YAML at all); newer exports may also carry Src/*.pa.yaml.
Controls/*.json is the complete source, so it is preferred; pa.yaml is used only
when no control JSON exists.

Cleaning drops what does not help a reader: embedded control definitions,
layout coordinates, static styling, values equal to the control's default, and
empty event handlers. Everything kept is still the app's real formula text.
"""
import hashlib
import html
import io
import json
import re
import zipfile

import fx

# ----------------------------------------------------------------------------- property classes

LAYOUT_PROPS = {
    "X", "Y", "Width", "Height", "ZIndex", "MinWidth", "MinHeight", "AlignInContainer", "FillPortions",
    "LayoutMinHeight", "LayoutMinWidth", "LayoutMaxHeight", "LayoutMaxWidth", "LayoutDirection",
    "LayoutJustifyContent", "LayoutAlignItems", "LayoutGap", "LayoutWrap", "LayoutOverflowX",
    "LayoutOverflowY", "LayoutMode", "TemplateSize", "TemplatePadding", "Size", "MinScreenHeight",
    "MinScreenWidth", "SizeBreakpoints", "Orientation", "WrapCount",
}
# Props that only ever hold presentation/runtime plumbing when constant.
NOISE_IF_STATIC = {
    "PCFTeamsTheme", "ContentLanguage", "AutoDisableOnSelect", "TabIndex", "DropShadow",
    "Transition", "DisplayMode", "Reset", "Live", "Role", "Wrap", "AutoHeight", "Overflow",
    "EnableSpellCheck", "Clear", "Underline", "Italic", "Strikethrough", "ShowScrollbar",
    "ShowNavigation", "NavigationStep", "Selectable", "LoadingSpinner", "LoadingSpinnerColor",
    "DelayOutput", "ImageRotation", "Transparency", "Appearance", "Layout", "ThemeName",
}
STYLE_RE = re.compile(
    r"(Color|Fill|Font|Border|Radius|Padding|Align|Shadow|ImagePosition|Thickness|Style|"
    r"Spacing|Margin|Weight|Icon|Image$|Size$|Disabled|Hover|Pressed|Focused|Indicator)"
)

FRIENDLY_TYPES = {
    "screen": "Screen", "appinfo": "App", "label": "Label (classic)", "button": "Button (classic)",
    "text": "Text input (classic)", "dropdown": "Drop down (classic)", "combobox": "Combo box (classic)",
    "gallery": "Gallery", "galleryTemplate": "Gallery template", "groupContainer": "Container",
    "group": "Group", "image": "Image", "icon": "Icon", "rectangle": "Rectangle", "form": "Edit form",
    "formViewer": "Display form", "typedDataCard": "Data card", "datepicker": "Date picker (classic)",
    "toggleSwitch": "Toggle (classic)", "checkbox": "Check box (classic)", "radio": "Radio (classic)",
    "timer": "Timer", "htmlViewer": "HTML text", "richTextEditor": "Rich text editor",
    "attachments": "Attachments", "hostControl": "Host (Teams/SharePoint form)",
    "pcfDataField": "Data field", "dataTable": "Data table", "slider": "Slider", "rating": "Rating",
    "lookupField": "Lookup field", "pdfViewer": "PDF viewer", "barcodeScanner": "Barcode scanner",
    "camera": "Camera", "addMedia": "Add picture", "penInput": "Pen input",
}


def friendly_type(template_name):
    if template_name in FRIENDLY_TYPES:
        return FRIENDLY_TYPES[template_name]
    m = re.match(r"Microsoft_CoreControls_(\w+)", template_name or "")
    if m:
        return re.sub(r"(?<!^)(?=[A-Z])", " ", m.group(1)) + " (modern)"
    m = re.match(r"PowerApps_CoreControls_(\w+)", template_name or "")
    if m:
        return re.sub(r"(?<!^)(?=[A-Z])", " ", m.group(1)) + " (modern)"
    return template_name or "unknown"


def is_behavior(prop):
    return prop.startswith("On") and len(prop) > 2 and prop[2].isupper()


# ----------------------------------------------------------------------------- zip handling

class Archive:
    """Read-only view of a zip with '/'-normalised paths (msapp entries often use backslashes)."""

    def __init__(self, data):
        self.zip = zipfile.ZipFile(io.BytesIO(data))
        self.names = {}
        for info in self.zip.infolist():
            if not info.is_dir():
                self.names[info.filename.replace("\\", "/")] = info.filename

    def has(self, path):
        return path in self.names

    def read(self, path):
        return self.zip.read(self.names[path])

    def json(self, path, default=None):
        if not self.has(path):
            return default
        raw = self.read(path)
        return json.loads(raw.decode("utf-8-sig"))

    def glob(self, prefix, suffix=""):
        return sorted(n for n in self.names if n.startswith(prefix) and n.endswith(suffix))


def open_input(path, app_hint=None):
    """Return (msapp_bytes, msapp_name, solution_archive_or_None)."""
    with open(path, "rb") as f:
        data = f.read()
    arc = Archive(data)
    if arc.has("Header.json") or arc.glob("Controls/") or arc.glob("Src/", ".pa.yaml"):
        return data, path.rsplit("/", 1)[-1], None
    apps = arc.glob("CanvasApps/", ".msapp")
    if not apps:
        raise SystemExit(f"{path}: not a .msapp and no CanvasApps/*.msapp inside (is it a solution export?)")
    if app_hint:
        apps = [a for a in apps if app_hint.lower() in a.lower()] or apps
    if len(apps) > 1 and not app_hint:
        print("Solution contains several canvas apps; documenting the first. Use --app to choose:")
        for a in apps:
            print("  ", a)
    return arc.read(apps[0]), apps[0].rsplit("/", 1)[-1], arc


# ----------------------------------------------------------------------------- template defaults

def _norm_default(v):
    v = html.unescape(v or "")
    v = re.sub(r"%(\w+)\.RESERVED%\.", r"\1.", v)
    return fx.normalize(v)


def classic_template_defaults(templates_json):
    out = {}
    for t in (templates_json or {}).get("UsedTemplates", []):
        props = {}
        for m in re.finditer(r'<property\s[^>]*?name="(\w+)"[^>]*?defaultValue="([^"]*)"', t.get("Template", "")):
            props[m.group(1)] = _norm_default(m.group(2))
        out[t.get("Name")] = props
    return out


def modern_template_defaults(template):
    raw = template.get("DynamicControlDefinitionJson")
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    props = {}
    for key in ("Properties", "IncludedProperties"):
        plist = d.get(key) or []
        if isinstance(plist, str):  # often double-encoded JSON
            try:
                plist = json.loads(plist)
            except ValueError:
                plist = []
        for p in plist:
            if isinstance(p, dict) and p.get("DefaultValue") is not None:
                props[p["Name"]] = fx.normalize(p["DefaultValue"])
    return props


def equals_default(value, default):
    if default is None:
        return False
    v, d = fx.normalize(value), fx.normalize(default)
    if v == d or v.replace(" ", "") == d.replace(" ", ""):
        return True
    # modern enum: script "'Microsoft.CoreControls.Button.ButtonType'.Primary" vs default "Primary"
    if "." in v and v.rsplit(".", 1)[-1] == d and re.fullmatch(r"\w+", d or ""):
        return True
    if d and v == f'"{d}"':
        return True
    return False


# ----------------------------------------------------------------------------- control tree

def keep_property(prop, value, category, default, symbols_re):
    """Decide whether a property assignment is worth showing to a reader."""
    v = fx.normalize(value)
    if v in ("", '""'):
        return False
    if is_behavior(prop):
        # Select(Parent) is gallery boilerplate that forwards a click to the gallery's own OnSelect.
        return v.lower() not in ("false", "true", "blank()") and v.replace(" ", "") != "Select(Parent)"
    if equals_default(v, default):
        return False
    if prop in LAYOUT_PROPS:
        return False  # positions/sizes, even responsive ones, are presentation not behaviour
    static = fx.is_static(v)
    dynamic = bool(fx.CONDITIONAL.search(fx.strip_strings(v))) or bool(symbols_re and symbols_re.search(fx.strip_strings(v)))
    if prop in NOISE_IF_STATIC or category == "Design" or STYLE_RE.search(prop):
        return not static and dynamic
    return True


def node_from_controls_json(n, templates, parent_path="", depth=0):
    t = n.get("Template", {}) or {}
    tname = t.get("Name", "")
    defaults = dict(templates.get(tname, {}))
    defaults.update(modern_template_defaults(t))
    path = f"{parent_path}/{n['Name']}" if parent_path else n["Name"]
    rules = []
    for r in n.get("Rules", []) or []:
        rules.append({"property": r.get("Property"), "formula": fx.normalize(r.get("InvariantScript")),
                      "category": r.get("Category", "")})
    return {
        "name": n["Name"],
        "template": tname,
        "type": friendly_type(tname),
        "template_version": t.get("Version", ""),
        "is_component_definition": bool(t.get("IsComponentDefinition")),
        "is_auto_generated": bool(n.get("IsAutoGenerated")),
        "variant": n.get("VariantName", ""),
        "path": path,
        "depth": depth,
        "rules": rules,
        "defaults": defaults,
        "children": [node_from_controls_json(c, templates, path, depth + 1) for c in n.get("Children", []) or []],
    }


def nodes_from_pa_yaml(arc):
    """Fallback for msapps that only carry Src/*.pa.yaml (Power Apps YAML schema v3)."""
    import yaml

    def conv(name, body, parent_path="", depth=0):
        body = body or {}
        ctrl = str(body.get("Control", "screen" if depth == 0 else "unknown"))
        tname = ctrl.split("@")[0]
        path = f"{parent_path}/{name}" if parent_path else name
        rules = []
        for k, v in (body.get("Properties") or {}).items():
            v = str(v)
            rules.append({"property": k, "formula": fx.normalize(v[1:] if v.startswith("=") else json.dumps(v)),
                          "category": ""})
        kids = []
        for item in body.get("Children") or []:
            for cname, cbody in item.items():
                kids.append(conv(cname, cbody, path, depth + 1))
        return {"name": name, "template": tname, "type": friendly_type(tname), "template_version": ctrl.partition("@")[2],
                "is_component_definition": False, "is_auto_generated": False, "variant": body.get("Variant", ""),
                "path": path, "depth": depth, "rules": rules, "defaults": {}, "children": kids}

    app, screens, comps = None, [], []
    for p in arc.glob("Src/", ".pa.yaml"):
        doc = yaml.safe_load(arc.read(p).decode("utf-8-sig")) or {}
        if "App" in doc:
            app = conv("App", doc["App"])
            app["template"], app["type"] = "appinfo", "App"
        for name, body in (doc.get("Screens") or {}).items():
            screens.append(conv(name, body))
        for name, body in (doc.get("ComponentDefinitions") or {}).items():
            c = conv(name, body)
            c["is_component_definition"] = True
            comps.append(c)
    return app, screens, comps


def walk(node):
    yield node
    for c in node["children"]:
        yield from walk(c)


def clean_tree(node, symbols_re):
    """Cleaned, reader-facing version of a control node (nested)."""
    props = {}
    for r in node["rules"]:
        if keep_property(r["property"], r["formula"], r["category"], node["defaults"].get(r["property"]), symbols_re):
            props[r["property"]] = r["formula"]
    out = {"name": node["name"], "type": node["type"]}
    if props:
        out["properties"] = props
    kids = [clean_tree(c, symbols_re) for c in node["children"]]
    if kids:
        out["children"] = kids
    return out


# ----------------------------------------------------------------------------- data sources

CONNECTOR_NAMES = {
    "shared_sharepointonline": "SharePoint", "shared_office365users": "Office 365 Users",
    "shared_office365": "Office 365 Outlook", "shared_commondataserviceforapps": "Dataverse",
    "shared_logicflows": "Power Automate flow", "shared_teams": "Microsoft Teams",
    "shared_sql": "SQL Server", "shared_excelonlinebusiness": "Excel Online (Business)",
    "shared_onedriveforbusiness": "OneDrive for Business", "shared_approvals": "Approvals",
    "shared_office365groups": "Office 365 Groups", "shared_planner": "Planner",
    "shared_powerbi": "Power BI", "shared_webcontents": "HTTP with Microsoft Entra ID",
}

SYSTEM_SP_COLUMNS = {"ID", "Created", "Modified", "Author", "Editor", "OData__ColorTag", "ComplianceAssetId",
                     "OData__UIVersionString", "GUID", "ContentType"}


def connector_name(api_id):
    tail = (api_id or "").rstrip("/").rsplit("/", 1)[-1]
    return CONNECTOR_NAMES.get(tail.lower(), tail.replace("shared_", "") or "unknown")


def _sp_column_type(name, p):
    t, fmt = p.get("type"), p.get("format")
    odata = json.dumps(p)
    if "SPListExpandedUser" in odata or (p.get("x-ms-displayFormat") or {}).get("subtitleProperty") == "Email":
        return "Person (multi)" if t == "array" else "Person"
    if "SPListExpandedReference" in odata:
        return "Choice/Lookup (multi)" if t == "array" else "Choice/Lookup"
    if "SPListExpandedTaxonomy" in odata:
        return "Managed metadata"
    if fmt == "date-time":
        return "Date/time"
    if fmt == "date":
        return "Date"
    if t == "integer" or (t == "number" and fmt in ("int64", "int32")):
        return "Number (integer)"
    if t == "number":
        return "Number"
    if t == "boolean":
        return "Yes/No"
    if t == "string":
        return "Text" if p.get("maxLength") else "Text (multi-line)"
    if t == "array":
        return "Array"
    if t == "object":
        return "Record"
    return t or "unknown"


def parse_tabular_schema(schema):
    items = schema.get("items", schema) if isinstance(schema, dict) else {}
    required = set(items.get("required", []) or [])
    cols = []
    for cname, p in (items.get("properties") or {}).items():
        if p.get("x-ms-visibility") == "internal" or "#Claims" in cname or cname.endswith("@odata.type"):
            continue
        cols.append({
            "name": cname,
            "display_name": p.get("title", cname),
            "type": _sp_column_type(cname, p),
            "required": cname in required,
            "read_only": p.get("x-ms-permission") == "read-only",
            "system": cname in SYSTEM_SP_COLUMNS or cname.startswith("{"),
            "primary_key": p.get("x-ms-keyType") == "primary",
            "max_length": p.get("maxLength"),
            "filterable": sorted((p.get("x-ms-capabilities") or {}).get("filterFunctions", []) or []),
            "description": p.get("description", ""),
        })
    return cols


def parse_static_schema(schema_str):
    types = {"s": "Text", "n": "Number", "b": "Boolean", "d": "Date/time", "i": "Image", "h": "Hyperlink",
             "c": "Color", "m": "Media", "o": "Record", "e": "Record"}
    cols = []
    for part in fx.split_top((schema_str or "").strip("*[] ")):
        if ":" in part:
            n, t = part.split(":", 1)
            cols.append({"name": n.strip(), "display_name": n.strip(), "type": types.get(t.strip()[:1], t.strip()),
                         "required": False, "read_only": False, "system": False, "primary_key": False})
    return cols


def parse_dataverse_metadata(meta):
    attrs = None
    stack = [meta]
    while stack and attrs is None:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() == "attributes" and isinstance(v, list):
                    attrs = v
                    break
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    cols = []
    for a in attrs or []:
        if not isinstance(a, dict):
            continue
        name = a.get("LogicalName") or a.get("logicalName")
        if not name:
            continue
        label = a.get("DisplayName")
        if isinstance(label, dict):
            label = ((label.get("UserLocalizedLabel") or {}).get("Label")) or name
        req = a.get("RequiredLevel")
        if isinstance(req, dict):
            req = req.get("Value")
        cols.append({"name": name, "display_name": label or name, "type": a.get("AttributeType", "unknown"),
                     "required": req in ("ApplicationRequired", "SystemRequired"),
                     "read_only": not a.get("IsValidForUpdate", True), "system": not a.get("IsCustomAttribute", True),
                     "primary_key": a.get("IsPrimaryId", False), "targets": a.get("Targets") or []})
    return cols


def parse_data_sources(ds_json):
    out = []
    for ds in (ds_json or {}).get("DataSources", []):
        typ = ds.get("Type", "")
        api = ds.get("ApiId", "")
        item = {"name": ds.get("Name"), "source_type": typ, "connector": connector_name(api) if api else "",
                "api_id": api, "is_sample": bool(ds.get("IsSampleData")), "writable": bool(ds.get("IsWritable")),
                "columns": [], "kind": ""}
        if typ == "ConnectedDataSourceInfo":
            item["kind"] = "SharePoint list" if "sharepoint" in api.lower() else "Connected table"
            item["site"] = ds.get("DatasetName", "")
            item["table_id"] = ds.get("TableName", "")
            for raw in (ds.get("DataEntityMetadataJson") or {}).values():
                try:
                    meta = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                item["display_name"] = meta.get("title", item["name"])
                item["web_url"] = meta.get("webUrl", "")
                item["columns"] = parse_tabular_schema(meta.get("schema", {}))
                item["referenced_entities"] = sorted((meta.get("referencedEntities") or {}).keys())
                break
        elif typ == "NativeCDSDataSourceInfo":
            item["kind"] = "Dataverse table"
            item["table_id"] = ds.get("LogicalName") or ds.get("TableName", "")
            for raw in (ds.get("DataEntityMetadataJson") or {}).values():
                try:
                    item["columns"] = parse_dataverse_metadata(json.loads(raw))
                except (TypeError, ValueError):
                    pass
                break
        elif typ == "ServiceInfo":
            item["kind"] = "Power Automate flow" if "logicflows" in api.lower() else "Connector"
            for k in ("FlowNameId", "WorkflowEntityId", "FlowId"):
                if ds.get(k):
                    item["flow_id"] = ds[k]
        elif typ == "StaticDataSourceInfo":
            item["kind"] = "Static/imported table"
            item["columns"] = parse_static_schema(ds.get("Schema", ""))
        elif typ == "CollectionDataSourceInfo":
            item["kind"] = "Collection"
        else:
            item["kind"] = typ.replace("DataSourceInfo", "") or "Other"
        out.append(item)
    return sorted(out, key=lambda d: (d["kind"], d["name"] or ""))


# ----------------------------------------------------------------------------- app checker

def parse_sarif(sarif):
    findings = []
    if not sarif:
        return findings
    for run in sarif.get("runs", []):
        rules = {}
        for r in ((run.get("tool") or {}).get("driver") or {}).get("rules", []) or []:
            rules[r.get("id")] = r
        for res in run.get("results", []) or []:
            loc = ""
            for l in res.get("locations", []) or []:
                ll = (l.get("logicalLocations") or [{}])[0]
                loc = ll.get("fullyQualifiedName", "")
                break
            rule = rules.get(res.get("ruleId"), {})
            desc = ((rule.get("shortDescription") or rule.get("fullDescription") or {}).get("text")
                    or (rule.get("messageStrings") or {}).get("issue", {}).get("text", ""))
            findings.append({"rule": res.get("ruleId", ""), "level": (res.get("properties") or {}).get("level")
                             or res.get("level") or "", "location": loc, "description": desc})
    return sorted(findings, key=lambda f: (f["rule"], f["location"]))


# ----------------------------------------------------------------------------- solution flows

def _flow_actions(actions, depth=0, out=None):
    out = [] if out is None else out
    for name, a in (actions or {}).items():
        a = a or {}
        inputs = a.get("inputs") or {}
        host = inputs.get("host") or {}
        op = host.get("operationId") or ""
        conn = (host.get("connection") or {}).get("name") or host.get("connectionName") or host.get("apiId") or ""
        out.append({"name": name, "type": a.get("type", ""), "depth": depth, "operation": op,
                    "connection": str(conn).rsplit("/", 1)[-1].strip("'@[]()"), "run_after": sorted((a.get("runAfter") or {}).keys())})
        _flow_actions(a.get("actions"), depth + 1, out)
        if isinstance(a.get("else"), dict):
            _flow_actions(a["else"].get("actions"), depth + 1, out)
        for case in (a.get("cases") or {}).values():
            _flow_actions((case or {}).get("actions"), depth + 1, out)
        if isinstance(a.get("default"), dict):
            _flow_actions(a["default"].get("actions"), depth + 1, out)
    return out


def parse_solution_flows(arc):
    if arc is None:
        return []
    names = {}
    if arc.has("customizations.xml"):
        xml = arc.read("customizations.xml").decode("utf-8-sig", "replace")
        for m in re.finditer(r'<Workflow\s+WorkflowId="\{?([0-9a-fA-F-]+)\}?"\s+Name="([^"]*)"', xml):
            names[m.group(1).lower()] = html.unescape(m.group(2))
    flows = []
    for p in arc.glob("Workflows/", ".json"):
        try:
            d = arc.json(p)
        except ValueError:
            continue
        props = d.get("properties", d)
        definition = props.get("definition") or {}
        guid = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})", p)
        guid = guid.group(1).lower() if guid else ""
        stem = p.rsplit("/", 1)[-1][:-5]
        display = names.get(guid) or re.sub(r"-?[0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27}$", "", stem)
        triggers = []
        for tname, t in (definition.get("triggers") or {}).items():
            t = t or {}
            triggers.append({"name": tname, "type": t.get("type", ""), "kind": t.get("kind", ""),
                             "inputs": sorted((((t.get("inputs") or {}).get("schema") or {}).get("properties") or {}).keys())})
        conns = sorted({(v or {}).get("api", {}).get("name") or k for k, v in (props.get("connectionReferences") or {}).items()})
        flows.append({"name": display, "file": p, "workflow_id": guid, "triggers": triggers,
                      "actions": _flow_actions(definition.get("actions")), "connections": conns})
    return sorted(flows, key=lambda f: f["name"].lower())


# ----------------------------------------------------------------------------- top level

def _parse_props_field(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            try:
                import ast
                return ast.literal_eval(v)
            except (ValueError, SyntaxError):
                return v
    return v


def load(path, app_hint=None):
    msapp_bytes, msapp_name, solution = open_input(path, app_hint)
    arc = Archive(msapp_bytes)
    header = arc.json("Header.json", {}) or {}
    props = arc.json("Properties.json", {}) or {}
    templates = classic_template_defaults(arc.json("References/Templates.json"))

    roots = [arc.json(p)["TopParent"] for p in arc.glob("Controls/", ".json")]
    source_format = "controls-json"
    app_node, screens, components = None, [], []
    if roots:
        for r in roots:
            node = node_from_controls_json(r, templates)
            node["_index"] = r.get("Index", 0)
            if node["template"] == "appinfo":
                app_node = node
            elif node["is_component_definition"]:
                components.append(node)
            else:
                screens.append(node)
    else:
        source_format = "pa-yaml"
        app_node, screens, components = nodes_from_pa_yaml(arc)
        for i, s in enumerate(screens):
            s["_index"] = i
    if not screens and not app_node:
        raise SystemExit("No screens found: unsupported .msapp layout (no Controls/*.json or Src/*.pa.yaml).")
    screens.sort(key=lambda s: (s.get("_index", 0), s["name"].lower()))
    components.sort(key=lambda s: s["name"].lower())
    if app_node is None:
        app_node = {"name": "App", "template": "appinfo", "type": "App", "rules": [], "defaults": {}, "children": [],
                    "path": "App", "depth": 0, "is_component_definition": False, "is_auto_generated": False,
                    "variant": "", "template_version": ""}

    data_sources = parse_data_sources(arc.json("References/DataSources.json"))
    resources = []
    for r in (arc.json("References/Resources.json", {}) or {}).get("Resources", []):
        if not r.get("IsSampleData"):
            resources.append({"name": r.get("Name"), "kind": r.get("Content", r.get("ResourceKind", "")),
                              "file": (r.get("FileName") or "").rsplit("/", 1)[-1]})
    resources.sort(key=lambda r: (r["kind"] or "", r["name"] or ""))

    pa_yaml_files = {p: arc.read(p).decode("utf-8-sig", "replace") for p in arc.glob("Src/", ".pa.yaml")}
    conn_refs = _parse_props_field(props.get("LocalConnectionReferences", "{}"))
    connections = []
    if isinstance(conn_refs, dict):
        for cid, c in sorted(conn_refs.items()):
            cr = (c or {}).get("connectionRef", {}) or {}
            connections.append({"connector": cr.get("displayName") or connector_name(cr.get("id", "")),
                                "data_sources": sorted((c or {}).get("dataSources", []) or [])})

    app_info = {
        "name": props.get("Name") or (arc.json("Resources/PublishInfo.json", {}) or {}).get("AppName") or msapp_name,
        "description": props.get("AppDescription", ""),
        "author": props.get("Author", ""),
        "app_type": props.get("DocumentAppType", ""),
        "layout": {"width": props.get("DocumentLayoutWidth"), "height": props.get("DocumentLayoutHeight"),
                   "orientation": props.get("DocumentLayoutOrientation"),
                   "scale_to_fit": props.get("DocumentLayoutScaleToFit")},
        "created_from": props.get("AppCreationSource", ""),
        "row_limit": props.get("DefaultConnectedDataSourceMaxGetRowsCount"),
        "doc_version": header.get("DocVersion", ""),
        "saved_utc": header.get("LastSavedDateTimeUTC", ""),
        "msapp_structure_version": header.get("MSAppStructureVersion", ""),
        "locale": (arc.json("Resources/PublishInfo.json", {}) or {}).get("UserLocale", ""),
        "connections": connections,
        "control_count_by_type": _parse_props_field(props.get("ControlCount", "{}")),
    }

    return {
        "source": {"file": path.rsplit("/", 1)[-1], "msapp": msapp_name,
                   "sha256": hashlib.sha256(msapp_bytes).hexdigest(), "format": source_format,
                   "raw_bytes": sum(i.file_size for i in arc.zip.infolist())},
        "app_info": app_info,
        "app_node": app_node,
        "screens": screens,
        "components": components,
        "data_sources": data_sources,
        "resources": resources,
        "app_checker": parse_sarif(arc.json("AppCheckerResult.sarif")),
        "solution_flows": parse_solution_flows(solution),
        "pa_yaml_files": pa_yaml_files,
    }
