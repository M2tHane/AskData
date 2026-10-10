"""Generate tokens.css from tokens.json (the single source). Run: python3 build_css.py [out.css ...]"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
d = json.loads((HERE / "tokens.json").read_text())


def block(sel: str, pairs: list[tuple[str, str]], extra: str = "", indent: str = "") -> str:
    body = "".join(f"{indent}  --{k}: {v};\n" for k, v in pairs)
    return f"{indent}{sel} {{\n{body}{indent}  {extra}\n{indent}}}\n" if extra else f"{indent}{sel} {{\n{body}{indent}}}\n"


static = [(t["name"], t["value"]) for g in ("spacing", "radius", "size") for t in d[g]["tokens"]]
static += [("font-sans", d["type"]["families"]["sans"]), ("font-mono", d["type"]["families"]["mono"])]
theme = {th: [] for th in ("light", "dark")}
for t in d["color"]["tokens"] + d["shadow"]["tokens"]:
    for th in theme:
        theme[th].append((t["name"], t["value"][th]))

css = "/* AskData — generated from tokens.json by build_css.py; do not edit by hand.\n"
css += "   Light is the default; dark via [data-theme=\"dark\"] on <html>, or automatically when the OS is dark and no data-theme is set. */\n"
css += block(":root", static)
css += block(':root, [data-theme="light"]', theme["light"], "color-scheme: light;")
css += block('[data-theme="dark"]', theme["dark"], "color-scheme: dark;")
css += "@media (prefers-color-scheme: dark) {\n" + block(":root:not([data-theme])", theme["dark"], "color-scheme: dark;", "  ") + "}\n"

for out in sys.argv[1:] or [str(HERE / "tokens.css")]:
    Path(out).write_text(css)
