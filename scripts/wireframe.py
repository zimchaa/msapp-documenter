"""Broad-strokes screen wireframes: evaluate each control's X/Y/Width/Height/Visible and lay it out.

Layout properties in canvas apps are usually formulas ('Title'.Y + 'Title'.Height + 20,
Parent.Width/4, If(App.ActiveScreen.Name = "...", ...)). This module evaluates the arithmetic
subset of Power Fx those formulas use, in the context of one screen at the app's design size,
and simulates auto-layout containers, galleries (first template row) and classic forms.
Anything it can't evaluate falls back to a sensible default and the box is marked approximate,
so the output is always a complete, deterministic picture - a sketch, not a pixel-perfect render.
"""
import re

import fx

FALLBACK = {"X": 0, "Y": 0, "Width": None, "Height": 40, "Visible": True}
TYPE_HEIGHT = {"button": 40, "text": 40, "label": 40, "dropdown": 40, "combobox": 40, "datepicker": 40,
               "toggleSwitch": 40, "checkbox": 32, "icon": 40, "image": 120, "gallery": 300, "form": 300,
               "htmlViewer": 100, "richTextEditor": 200, "rectangle": 4}


class Unknown(Exception):
    pass


# ----------------------------------------------------------------------------- tiny expression evaluator

TOKEN = re.compile(r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?%?) |
    (?P<str>"(?:[^"]|"")*") |
    (?P<qid>'(?:[^']|'')*') |
    (?P<id>[A-Za-z_][A-Za-z0-9_]*) |
    (?P<op><=|>=|<>|&&|\|\||[-+*/(),.<>=!&^])
)""", re.X)


def tokenize(s):
    out, pos = [], 0
    s = re.sub(r"//[^\n]*", "", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    while pos < len(s):
        if s[pos:].strip() == "":
            break
        m = TOKEN.match(s, pos)
        if not m or m.end() == pos:
            raise Unknown(f"token at {s[pos:pos + 10]!r}")
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        if kind == "num":
            out.append(("num", float(val[:-1]) / 100 if val.endswith("%") else float(val)))
        elif kind == "str":
            out.append(("str", val[1:-1].replace('""', '"')))
        elif kind == "qid":
            out.append(("id", val[1:-1].replace("''", "'")))
        elif kind == "id":
            out.append(("id", val))
        else:
            out.append(("op", val))
    return out


class Parser:
    """Recursive descent over tokens; `resolve(path)` supplies values for dotted names."""

    def __init__(self, tokens, resolve):
        self.t, self.i, self.resolve = tokens, 0, resolve

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, val=None):
        tok = self.peek()
        if val is not None and tok[1] != val:
            raise Unknown(f"expected {val}")
        self.i += 1
        return tok

    def parse(self):
        v = self.expr()
        if self.i != len(self.t):
            raise Unknown("trailing tokens")
        return v

    def expr(self):
        return self.orx()

    def orx(self):
        v = self.andx()
        while self.peek()[1] in ("||",) or (self.peek()[0] == "id" and self.peek()[1] == "Or"):
            self.take()
            r = self.andx()
            v = bool(v) or bool(r)
        return v

    def andx(self):
        v = self.cmp()
        while self.peek()[1] in ("&&",) or (self.peek()[0] == "id" and self.peek()[1] == "And"):
            self.take()
            r = self.cmp()
            v = bool(v) and bool(r)
        return v

    def cmp(self):
        v = self.add()
        while self.peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = self.take()[1]
            r = self.add()
            v = {"=": v == r, "<>": v != r, "<": v < r, ">": v > r, "<=": v <= r, ">=": v >= r}[op]
        return v

    def add(self):
        v = self.mul()
        while self.peek()[1] in ("+", "-", "&"):
            op = self.take()[1]
            r = self.mul()
            if op == "&":
                v = f"{v}{r}"
            else:
                v = v + r if op == "+" else v - r
        return v

    def mul(self):
        v = self.unary()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            r = self.unary()
            v = v * r if op == "*" else (v / r if r else 0)
        return v

    def unary(self):
        if self.peek()[1] == "-":
            self.take()
            return -self.unary()
        if self.peek()[1] == "!" or (self.peek()[0] == "id" and self.peek()[1] == "Not" and self.peek(1)[1] != "("):
            self.take()
            return not self.unary()
        return self.atom()

    def args(self):
        self.take("(")
        out = []
        if self.peek()[1] != ")":
            out.append(self.expr())
            while self.peek()[1] == ",":
                self.take()
                out.append(self.expr())
        self.take(")")
        return out

    def lazy_args(self):
        """Raw token slices per argument so If/Switch only evaluate the branch taken."""
        self.take("(")
        groups, depth, cur = [], 0, []
        while True:
            tok = self.take()
            if tok[0] is None:
                raise Unknown("unbalanced")
            if tok[1] == "(":
                depth += 1
            elif tok[1] == ")":
                if depth == 0:
                    groups.append(cur)
                    return groups
                depth -= 1
            elif tok[1] == "," and depth == 0:
                groups.append(cur)
                cur = []
                continue
            cur.append(tok)

    def sub(self, toks):
        return Parser(toks, self.resolve).parse()

    def atom(self):
        kind, val = self.peek()
        if kind == "num" or kind == "str":
            self.take()
            return val
        if val == "(":
            self.take()
            v = self.expr()
            self.take(")")
            return v
        if kind == "id":
            self.take()
            if self.peek()[1] == "(":
                if val in ("If", "Switch"):
                    groups = self.lazy_args()
                    if val == "If":
                        for i in range(0, len(groups) - 1, 2):
                            if self.sub(groups[i]):
                                return self.sub(groups[i + 1])
                        return self.sub(groups[-1]) if len(groups) % 2 else 0
                    subject = self.sub(groups[0])
                    rest = groups[1:]
                    for i in range(0, len(rest) - 1, 2):
                        if self.sub(rest[i]) == subject:
                            return self.sub(rest[i + 1])
                    return self.sub(rest[-1]) if len(rest) % 2 else 0
                a = self.args()
                fns = {"Max": lambda *x: max(x), "Min": lambda *x: min(x), "Abs": lambda x: abs(x),
                       "RoundDown": lambda x, d=0: float(int(x)), "RoundUp": lambda x, d=0: float(-int(-x // 1)),
                       "Round": lambda x, d=0: float(round(x)), "Not": lambda x: not x, "Sqrt": lambda x: x ** 0.5,
                       "And": lambda *x: all(x), "Or": lambda *x: any(x), "Value": lambda x: float(x)}
                if val in fns:
                    return fns[val](*a)
                raise Unknown(f"function {val}")
            if val in ("true", "false"):
                return val == "true"
            path = [val]
            while self.peek()[1] == ".":
                self.take()
                path.append(self.take()[1])
            return self.resolve(path)
        raise Unknown(f"atom {val!r}")


def evaluate(formula, resolve):
    return Parser(tokenize(formula), resolve).parse()


# ----------------------------------------------------------------------------- layout

def _num(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    raise Unknown("not a number")


def _static_text(formula):
    f = fx.normalize(formula)
    if re.fullmatch(r'"(?:[^"]|"")*"', f):
        return f[1:-1].replace('""', '"')
    return None


class ScreenLayout:
    def __init__(self, screen, app_w, app_h, all_controls, constants=None):
        self.constants = constants or {}
        self.screen = screen
        self.app = {"Width": float(app_w), "Height": float(app_h), "MinScreenWidth": 320.0, "MinScreenHeight": 320.0,
                    "DesignWidth": float(app_w), "DesignHeight": float(app_h)}
        self.nodes = {}
        self.parent = {}
        self.cache = {}
        self.busy = set()
        self.approx = set()
        self.auto_pos = {}
        self.runtime_visible = set()
        for n, p in _walk_with_parent(screen, None):
            self.nodes[n["name"]] = n
            self.parent[n["name"]] = p
        self.all = all_controls

    # -- property access ---------------------------------------------------------------
    def rule(self, node, prop):
        for r in node["rules"]:
            if r["property"] == prop:
                return r["formula"]
        return node["defaults"].get(prop)

    def get(self, name, prop):
        key = (name, prop)
        if key in self.cache:
            return self.cache[key]
        if key in self.busy:
            raise Unknown("cycle")
        node = self.nodes.get(name)
        if node is None:
            raise Unknown(f"control {name} not on this screen")
        self.busy.add(key)
        try:
            val = self._compute(node, prop)
        finally:
            self.busy.discard(key)
        self.cache[key] = val
        return val

    def _compute(self, node, prop):
        name = node["name"]
        if node is self.screen:
            if prop in ("Width", "Height"):
                return self.app[prop]
            if prop in ("X", "Y"):
                return 0.0
            if prop == "Visible":
                return True
        if prop in ("X", "Y") and name in self.auto_pos:
            return self.auto_pos[name][prop]
        f = self.rule(node, prop)
        if f is not None and str(f).strip() != "":
            try:
                v = evaluate(f, lambda path: self.resolve(node, path))
                return bool(v) if prop == "Visible" else _num(v)
            except (Unknown, TypeError, ZeroDivisionError, ValueError, IndexError, RecursionError):
                if prop == "Visible":
                    self.runtime_visible.add(name)  # depends on data/variables: shown, marked conditional
                    return True
        elif prop in ("Visible", "X", "Y"):
            return True if prop == "Visible" else 0.0  # absent = the control default
        self.approx.add(name)
        if prop == "Visible":
            return True
        if prop == "Width":
            par = self.parent[name]
            return self.get(par["name"], "Width") if par else self.app["Width"]
        if prop == "Height":
            return float(TYPE_HEIGHT.get(node["template"], 40))
        if prop in ("TemplateSize", "TemplateHeight"):
            return 80.0
        return float(FALLBACK.get(prop, 0) or 0)

    def resolve(self, node, path):
        head = path[0]
        if head == "Self":
            return self.prop_of(node, path[1:])
        if head == "Parent":
            par = self.parent[node["name"]]
            if par is None:
                raise Unknown("no parent")
            if par["template"] == "galleryTemplate":
                par = self.parent[par["name"]]
            return self.prop_of(par, path[1:])
        if head == "App":
            if len(path) == 3 and path[1] == "ActiveScreen" and path[2] == "Name":
                return self.screen["name"]
            if len(path) == 2 and path[1] in self.app:
                return self.app[path[1]]
            raise Unknown("App." + ".".join(path[1:]))
        if head in self.nodes and len(path) == 2:
            return self.prop_of(self.nodes[head], path[1:])
        if len(path) == 1 and head in self.constants:
            return self.constants[head]
        if head == "ThisItem" or head == "ThisRecord":
            raise Unknown("data-bound")
        raise Unknown(".".join(path))

    def prop_of(self, node, rest):
        if len(rest) != 1:
            if len(rest) == 1 + 1 and rest == ["Size", "Width"]:
                pass
            raise Unknown("deep member")
        p = rest[0]
        if p in ("TemplateWidth",):
            return self.get(node["name"], "Width")
        if p in ("TemplateHeight",):
            return self.get(node["name"], "TemplateSize")
        if p == "Name":
            return node["name"]
        if p in ("X", "Y", "Width", "Height", "Visible", "TemplateSize", "PaddingTop", "PaddingBottom",
                 "PaddingLeft", "PaddingRight", "FillPortions", "LayoutGap", "Size"):
            return self.get(node["name"], p)
        raise Unknown(f"property {p}")

    # -- auto layout --------------------------------------------------------------------
    def _layout_auto(self, cont):
        mode = (self.rule(cont, "LayoutMode") or "")
        variant = cont.get("variant", "")
        if "Auto" not in mode and "AutoLayout" not in variant:
            return False
        horizontal = "Horizontal" in (self.rule(cont, "LayoutDirection") or "") or variant.startswith("horizontal")
        kids = [c for c in cont["children"] if self._visible(c)]
        try:
            gap = _num(evaluate(self.rule(cont, "LayoutGap") or "0", lambda p: self.resolve(cont, p)))
        except Unknown:
            gap = 0.0
        main = "Width" if horizontal else "Height"
        total = self.get(cont["name"], main) - gap * max(len(kids) - 1, 0)
        fixed, portions = 0.0, {}
        for c in kids:
            fp = self.rule(c, "FillPortions")
            try:
                fpv = _num(evaluate(fp, lambda p: self.resolve(c, p))) if fp else 0.0
            except Unknown:
                fpv = 0.0
            if fpv > 0:
                portions[c["name"]] = fpv
            else:
                fixed += self.get(c["name"], main)
        share = max(total - fixed, 0) / (sum(portions.values()) or 1)
        pos = 0.0
        for c in kids:
            if c["name"] in portions:
                self.cache[(c["name"], main)] = share * portions[c["name"]]
            self.auto_pos[c["name"]] = {"X": pos if horizontal else 0.0, "Y": 0.0 if horizontal else pos}
            pos += self.get(c["name"], main) + gap
        return True

    def _visible(self, node):
        try:
            return bool(self.get(node["name"], "Visible"))
        except Unknown:
            return True

    def _conditional_visible(self, node):
        return node["name"] in self.runtime_visible

    # -- walk -----------------------------------------------------------------------------
    def boxes(self):
        out = []

        def place(node, ox, oy, depth, clip):
            name = node["name"]
            if node["template"] == "galleryTemplate":
                return
            if not self._visible(node):
                return
            if node is self.screen:
                x, y, w, h = 0.0, 0.0, self.app["Width"], self.app["Height"]
            elif node["template"] == "group":
                x, y = ox, oy
                w = h = 0.0
            else:
                x = ox + self.get(name, "X")
                y = oy + self.get(name, "Y")
                w = self.get(name, "Width")
                h = self.get(name, "Height")
            if node is not self.screen and node["template"] != "group":
                cx, cy, cw, ch = clip
                x0, y0 = max(x, cx), max(y, cy)
                x1, y1 = min(x + w, cx + cw), min(y + h, cy + ch)
                if x1 - x0 >= 1 and y1 - y0 >= 1:
                    out.append(self._box(node, x0, y0, x1 - x0, y1 - y0, depth))
            if not node["children"]:
                return
            if node["template"] in ("groupContainer",):
                self._layout_auto(node)
            inner_clip = clip if node["template"] == "group" else (x, y, w, h)
            if node["template"] == "gallery":
                tsize = self.get(name, "TemplateSize")
                rows = max(1, int(h // max(tsize, 1)))
                out[-1]["rows"] = min(rows, 12)
                out[-1]["row_height"] = round(tsize, 1)
                for c in node["children"]:
                    place(c, x, y, depth + 1, (x, y, w, min(tsize, h)))
                return
            if node["template"] in ("form", "formViewer"):
                self._layout_form(node)
            for c in node["children"]:
                place(c, x if node["template"] != "group" else ox, y if node["template"] != "group" else oy,
                      depth + 1, inner_clip)

        place(self.screen, 0.0, 0.0, 0, (0.0, 0.0, self.app["Width"], self.app["Height"]))
        return out

    def _layout_form(self, form):
        """Classic forms: data card X/Y are grid column/row indexes, not pixels."""
        cards = [c for c in form["children"] if c["template"] in ("typedDataCard",) or "DataCard" in c["template"]]
        if not cards:
            return
        try:
            cols = max(1, int(_num(evaluate(self.rule(form, "Columns") or "1", lambda p: self.resolve(form, p)))))
        except Unknown:
            cols = 1
        fw = self.get(form["name"], "Width")
        grid = []
        for c in cards:
            try:
                gx = int(_num(evaluate(self.rule(c, "X") or "0", lambda p: self.resolve(c, p))))
                gy = int(_num(evaluate(self.rule(c, "Y") or "0", lambda p: self.resolve(c, p))))
            except Unknown:
                gx, gy = 0, len(grid)
            grid.append((gy, gx, c))
        y, last_row, row_h = 0.0, None, 0.0
        for gy, gx, c in sorted(grid, key=lambda g: (g[0], g[1])):
            if gy != last_row:
                y += row_h
                row_h, last_row = 0.0, gy
            w = fw / cols
            self.cache[(c["name"], "Width")] = w
            h = self.get(c["name"], "Height")
            row_h = max(row_h, h)
            self.auto_pos[c["name"]] = {"X": gx * w, "Y": y}

    def _box(self, node, x, y, w, h, depth):
        text = None
        for prop in ("Text", "HintText", "Placeholder", "Label"):
            f = self.rule(node, prop)
            if f:
                st = _static_text(f)
                if st is None:
                    try:  # e.g. Switch(App.ActiveScreen.Name, "Step 1 Screen", "General information", ...)
                        v = evaluate(f, lambda path: self.resolve(node, path))
                        st = v if isinstance(v, str) else None
                    except (Unknown, TypeError, ZeroDivisionError, ValueError, IndexError, RecursionError):
                        st = None
                if st is not None:
                    text = st
                elif not fx.is_static(f):
                    m = re.fullmatch(r"\s*(ThisItem|ThisRecord)\.('?[^']+'?|\w+)\s*", f)
                    text = "{" + fx.unquote_ident(m.group(2)) + "}" if m else "ƒx"
                if text:
                    break
        if text is None and node["template"] in ("Microsoft_CoreControls_RadioGroup", "radio", "dropdown",
                                                 "Microsoft_CoreControls_ComboBox", "combobox", "Microsoft_CoreControls_DropDown"):
            items = self.rule(node, "Items")
            lits = fx.string_literals(items or "")
            if lits:
                text = " / ".join(lits[:4])
        return {"name": node["name"], "type": node["type"], "template": node["template"], "kind": kind_of(node["template"]),
                "x": round(x), "y": round(y), "w": round(w), "h": round(h), "depth": depth,
                "text": (text or "")[:80], "conditional": self._conditional_visible(node),
                "approx": node["name"] in self.approx}


def kind_of(template):
    t = template.lower()
    for key, kind in (("richtext", "richtext"), ("button", "button"), ("textbox", "input"), ("textinput", "input"), ("text", "input"),
                      ("combobox", "dropdown"), ("dropdown", "dropdown"), ("radio", "radio"), ("checkbox", "checkbox"),
                      ("toggle", "toggle"), ("datepicker", "input"), ("richtext", "richtext"), ("label", "label"),
                      ("htmlviewer", "label"), ("gallery", "gallery"), ("image", "image"), ("icon", "icon"),
                      ("rectangle", "shape"), ("circle", "shape"), ("groupcontainer", "container"),
                      ("container", "container"), ("form", "form"), ("datacard", "container"),
                      ("pcfdatafield", "skip"), ("hostcontrol", "skip"), ("timer", "skip"), ("attachments", "input"),
                      ("rating", "input"), ("slider", "input"), ("pdf", "image"), ("camera", "image"),
                      ("barcode", "button"), ("addmedia", "image")):
        if key in t:
            return kind
    return "other"


def _walk_with_parent(node, parent):
    yield node, parent
    for c in node["children"]:
        yield from _walk_with_parent(c, node)


def constants_from(app_node):
    """Global variables set once to a literal in App.OnStart, e.g. Set(_created, "v1.3")."""
    out = {}
    for r in app_node["rules"]:
        if r["property"] != "OnStart":
            continue
        for _, args, _, _ in fx.calls(r["formula"], "Set"):
            if len(args) == 2:
                st = _static_text(args[1])
                if st is not None:
                    out[fx.unquote_ident(args[0])] = st
                elif re.fullmatch(r"-?\d+(\.\d+)?", args[1].strip()):
                    out[fx.unquote_ident(args[0])] = float(args[1])
    return out


def outline(layout):
    """Compact text form of a wireframe for AI context: one line per visible control."""
    lines = []
    for b in layout["boxes"]:
        if b["kind"] == "container" and not b["text"]:
            continue
        flags = (" (shown conditionally)" if b["conditional"] else "") + (" (position approximate)" if b["approx"] else "")
        text = f' "{b["text"]}"' if b["text"] else ""
        ref = f'[#{b["callout"]}] ' if b.get("callout") else ""
        lines.append(f'{"  " * max(b["depth"] - 1, 0)}- {ref}{b["kind"]}: {b["name"]}{text} @ x{b["x"]} y{b["y"]} {b["w"]}x{b["h"]}{flags}')
    return "\n".join(lines)


def screen_boxes(screen, app_w, app_h, all_controls=None, constants=None):
    lay = ScreenLayout(screen, app_w or 1366, app_h or 768, all_controls or {}, constants)
    boxes = [b for b in lay.boxes() if b["kind"] != "skip"]
    return {"width": round(app_w or 1366), "height": round(app_h or 768), "boxes": boxes,
            "approx_count": sum(1 for b in boxes if b["approx"])}


# ----------------------------------------------------------------------------- SVG output

def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def svg(layout, title=""):
    W, H = layout["width"], layout["height"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 -30 {W + 20} {H + 40}" width="{W + 20}" height="{H + 40}" '
           f'font-family="Segoe UI, Helvetica, Arial, sans-serif">',
           f'<text x="0" y="-10" font-size="16" font-weight="600" fill="#333">{_esc(title)}</text>',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff" stroke="#9aa3ad" stroke-width="2" rx="6"/>']
    for b in layout["boxes"]:
        x, y, w, h, k = b["x"], b["y"], b["w"], b["h"], b["kind"]
        dash = ' stroke-dasharray="6 4"' if b["conditional"] or b["approx"] else ""
        fs = max(9, min(16, h * 0.45))
        txt = _esc(b["text"] or "")
        cx, cy = x + w / 2, y + h / 2 + fs * 0.35
        if k == "container":
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#d5dae0" stroke-width="1"{dash}/>')
        elif k == "gallery":
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#f6f8fa" stroke="#7a8794" stroke-width="1.5"{dash}/>')
            rh = b.get("row_height") or 0
            for r in range(1, b.get("rows", 1)):
                ly = y + r * rh
                if ly < y + h:
                    out.append(f'<line x1="{x + 6}" y1="{ly}" x2="{x + w - 6}" y2="{ly}" stroke="#c4ccd4" stroke-dasharray="3 3"/>')
            out.append(f'<text x="{x + w - 6}" y="{y + 14}" font-size="10" fill="#7a8794" text-anchor="end">gallery</text>')
        elif k == "button":
            edge = ' stroke="#22324a"' + dash if dash else ""
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="#4a6fa5"{edge}/>')
            out.append(f'<text x="{cx}" y="{cy}" font-size="{fs}" fill="#fff" text-anchor="middle">{txt or "Button"}</text>')
        elif k in ("input", "dropdown", "richtext"):
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="#fff" stroke="#7a8794" stroke-width="1.2"{dash}/>')
            hint = txt or {"input": "text input", "dropdown": "select…", "richtext": "rich text"}[k]
            out.append(f'<text x="{x + 8}" y="{y + min(h, 32) / 2 + fs * 0.35}" font-size="{fs}" fill="#9aa3ad">{hint}</text>')
            if k == "dropdown":
                out.append(f'<text x="{x + w - 16}" y="{y + min(h, 32) / 2 + fs * 0.35}" font-size="{fs}" fill="#7a8794">▾</text>')
        elif k in ("radio", "checkbox", "toggle"):
            mark = {"radio": "◉", "checkbox": "☑", "toggle": "⬭"}[k]
            out.append(f'<text x="{x + 4}" y="{y + fs + 4}" font-size="{fs}" fill="#333">{mark} {txt or k}</text>')
        elif k in ("image", "icon"):
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#eef1f4" stroke="#b5bec7"{dash}/>')
            out.append(f'<line x1="{x}" y1="{y}" x2="{x + w}" y2="{y + h}" stroke="#c9d0d7"/><line x1="{x + w}" y1="{y}" x2="{x}" y2="{y + h}" stroke="#c9d0d7"/>')
        elif k == "shape":
            out.append(f'<rect x="{x}" y="{y}" width="{max(w, 1)}" height="{max(h, 1)}" fill="#c4ccd4"/>')
        elif k == "form":
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fbfcfd" stroke="#7a8794"{dash}/>')
        else:
            if txt:
                style = ' fill="#9aa3ad" font-style="italic"' if txt == "ƒx" else ' fill="#333"'
                out.append(f'<text x="{x + 2}" y="{y + min(h, fs * 1.6) / 2 + fs * 0.35}" font-size="{fs}"{style}>{txt}</text>')
            if dash:
                out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#c4ccd4"{dash}/>')
    for b in layout["boxes"]:
        if b.get("callout"):
            mx, my = marker_pos(b, W)
            out.append(f'<circle cx="{mx}" cy="{my}" r="{MARKER_R}" fill="{MARKER_COLOR}" stroke="#ffffff" stroke-width="2"/>')
            out.append(f'<text x="{mx}" y="{my + 5}" font-size="14" font-weight="700" fill="#ffffff" text-anchor="middle">{b["callout"]}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


MARKER_COLOR = "#FF3D7F"   # deliberately off-palette so callouts stand out on any template
MARKER_R = 13


def marker_pos(b, screen_w):
    """Marker centre: just outside the control's top-left corner, kept inside the screen."""
    x = b["x"] - MARKER_R + 4 if b["x"] >= MARKER_R else b["x"] + MARKER_R
    y = b["y"] - MARKER_R + 4 if b["y"] >= MARKER_R else b["y"] + MARKER_R
    return min(max(x, MARKER_R), screen_w - MARKER_R), y
