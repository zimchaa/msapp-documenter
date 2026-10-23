"""Small, dependency-free helpers for reading Power Fx formulas.

This is not a full Power Fx parser. It understands just enough structure
(strings, quoted identifiers, nesting) to find function calls, split their
arguments and look up references reliably and deterministically.
"""
import re

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


def normalize(formula):
    """Canonical text for a formula: LF line endings, no trailing spaces, tabs as spaces."""
    if formula is None:
        return ""
    s = str(formula).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return "\n".join(line.rstrip() for line in s.split("\n")).strip()


def _scan(s):
    """Yield (index, char, in_code) where in_code is False inside "strings" and 'identifiers'."""
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            q = c
            yield i, c, False
            i += 1
            while i < n:
                if s[i] == q:
                    if i + 1 < n and s[i + 1] == q:  # doubled quote = escaped
                        yield i, s[i], False
                        yield i + 1, s[i + 1], False
                        i += 2
                        continue
                    yield i, s[i], False
                    i += 1
                    break
                yield i, s[i], False
                i += 1
            continue
        if c == "/" and s[i:i + 2] == "//":
            j = s.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                yield k, s[k], False
            i = j
            continue
        if c == "/" and s[i:i + 2] == "/*":
            j = s.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                yield k, s[k], False
            i = j
            continue
        yield i, c, True
        i += 1


def strip_strings(s):
    """Replace the contents of "string literals" and comments with spaces (keeps 'quoted identifiers')."""
    out = list(s)
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "'":
            j = i + 1
            while j < n:
                if s[j] == "'":
                    if j + 1 < n and s[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if s[j] == '"':
                    if j + 1 < n and s[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        if s[i:i + 2] == "//":
            j = s.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if s[i:i + 2] == "/*":
            j = s.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def string_literals(s):
    """All "string literal" values in a formula."""
    res, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "'":
            j = i + 1
            while j < n and not (s[j] == "'" and not (j + 1 < n and s[j + 1] == "'")):
                j += 2 if s[j] == "'" else 1
            i = j + 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n:
                if s[j] == '"':
                    if j + 1 < n and s[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(s[j])
                j += 1
            res.append("".join(buf))
            i = j + 1
            continue
        i += 1
    return res


def matching_close(s, open_idx):
    """Index of the bracket closing the one at open_idx, or len(s)-1 if unbalanced."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    depth = 0
    for i, c, code in _scan(s[open_idx:]):
        if not code:
            continue
        if c in pairs:
            depth += 1
        elif c in ")}]":
            depth -= 1
            if depth == 0:
                return open_idx + i
    return len(s) - 1


def split_top(s, sep=","):
    """Split on sep at nesting depth 0, ignoring separators inside strings/identifiers."""
    parts, depth, last = [], 0, 0
    for i, c, code in _scan(s):
        if not code:
            continue
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(s[last:i])
            last = i + 1
    parts.append(s[last:])
    return [p.strip() for p in parts if p.strip() != "" or len(parts) > 1]


def calls(formula, names):
    """Find calls to any function in `names`. Returns [(name, [arg strings], start, end)]."""
    if isinstance(names, str):
        names = [names]
    code = strip_strings(formula)
    pat = re.compile(r"(?<![\w.'])(" + "|".join(re.escape(n) for n in names) + r")\s*\(")
    out = []
    for m in pat.finditer(code):
        open_idx = m.end() - 1
        close = matching_close(formula, open_idx)
        out.append((m.group(1), split_top(formula[open_idx + 1:close]), m.start(), close))
    return out


def unquote_ident(token):
    token = token.strip()
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1].replace("''", "'")
    return token


def ident_pattern(name):
    """Regex matching a reference to `name` (bare or 'quoted'), not as a .member or record field name."""
    q = "'" + re.escape(name.replace("'", "''")) + "'"
    if re.fullmatch(IDENT, name):
        return re.compile(r"(?:" + q + r"|(?<![\w.'])" + re.escape(name) + r"(?![\w']))")
    return re.compile(q)


def references(formula, name, code=None):
    """Count references to an identifier in a formula, ignoring string contents."""
    code = strip_strings(formula) if code is None else code
    return len(ident_pattern(name).findall(code))


ENUM_ROOTS = {
    "Color", "Font", "FontWeight", "Align", "VerticalAlign", "ImagePosition", "BorderStyle",
    "DisplayMode", "TextFormat", "LayoutDirection", "LayoutJustifyContent", "LayoutAlignItems",
    "LayoutOverflow", "LayoutMode", "Transition", "ScreenTransition", "Icon", "TextMode",
    "Direction", "Overflow", "TeamsTheme", "TextPosition", "AlignInContainer", "DateTimeZone",
    "Themes", "PenMode", "TextInputType", "DropShadow", "ListItemTemplate", "Layout",
    "GridStyle", "ScreenSize", "ColorValue",
}

_STATIC_PATTERNS = [
    re.compile(r"-?\d+(\.\d+)?%?"),
    re.compile(r'"(?:[^"]|"")*"'),
    re.compile(r"(?i)true|false|blank\(\)"),
    re.compile(r"RGBA\(\s*[\d.\s]+,\s*[\d.\s]+,\s*[\d.\s]+,\s*[\d.\s%]+\)"),
    re.compile(r'ColorValue\(\s*"[^"]*"\s*\)'),
    re.compile(r"(?:" + IDENT + r"|'[^']+')(?:\." + IDENT + r")+"),
]


def is_static(formula):
    """True for literals and enum values (e.g. 12, "x", RGBA(...), Font.Lato, 'Microsoft.X.Y'.Z)."""
    f = normalize(formula)
    if not f:
        return True
    for p in _STATIC_PATTERNS[:5]:
        if p.fullmatch(f):
            return True
    if _STATIC_PATTERNS[5].fullmatch(f):
        root = unquote_ident(re.split(r"\.(?=[A-Za-z_])", f, maxsplit=1)[0] if not f.startswith("'") else f[: f.index("'", 1) + 1])
        return root in ENUM_ROOTS or root.startswith("Microsoft.") or root.startswith("PowerAppsOneGrid")
    return False


CONDITIONAL = re.compile(
    r"(?<![\w.])(If|Switch|IsBlank|IsEmpty|LookUp|Filter|ThisItem|ThisRecord|User|Today|Now|"
    r"Coalesce|IsMatch|CountRows)\b|App\.ActiveScreen|\.Selected\b|\.IsSelected\b|\.Value\b|\.Text\b"
)
