"""Generate the README chart, one SVG per colour scheme.

Two panels rather than one shared axis: 77 and 6 on the same scale would
flatten the second comparison into a sliver. Each panel is its own chart with
its own scale, which is the honest way to show two measures of different
magnitude side by side.
"""

from pathlib import Path

THEMES = {
    "light": {
        "surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e",
        "flagged": "#eb6834", "real": "#2a78d6", "rule": "#dcdcd8",
    },
    "dark": {
        "surface": "#1a1a19", "text": "#ffffff", "muted": "#c3c2b7",
        "flagged": "#d95926", "real": "#3987e5", "rule": "#3a3a38",
    },
}

PANELS = [
    {"title": "Duplicate clusters, Spider train",
     "flagged": 77, "real": 8, "factor": "9x",
     "note": "69 reuse a question against a different database"},
    {"title": "Contaminated dev items, Spider",
     "flagged": 6, "real": 2, "factor": "3x",
     "note": "4 match on question text but resolve to different SQL"},
]

W, H = 900, 300
BAR_MAX = 250
BAR_H = 26
FONT = ("system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,"
        "sans-serif")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bar(x, y, width, colour):
    """Rounded data-end, square against the baseline it grows from."""
    r = 4
    if width <= r:
        return f'<rect x="{x}" y="{y}" width="{max(width, 1)}" height="{BAR_H}" fill="{colour}"/>'
    return (f'<path d="M{x},{y} H{x + width - r} A{r},{r} 0 0 1 {x + width},{y + r} '
            f'V{y + BAR_H - r} A{r},{r} 0 0 1 {x + width - r},{y + BAR_H} '
            f'H{x} Z" fill="{colour}"/>')


def build(theme_name):
    t = THEMES[theme_name]
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="{W}" height="{H}" role="img" '
         f'aria-label="Naive duplicate detection overstates the problem: '
         f'77 flagged clusters versus 8 real in Spider train, and 6 versus 2 '
         f'contaminated dev items.">',
         f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>',
         f'<g font-family="{FONT}">']

    o.append(f'<text x="40" y="44" font-size="20" font-weight="600" '
             f'fill="{t["text"]}">The obvious method overstates the problem</text>')
    o.append(f'<text x="40" y="68" font-size="14" fill="{t["muted"]}">'
             f'Requiring the reference answer to match, not just the question</text>')

    # Shared legend, top right.
    lx = W - 40
    o.append(f'<circle cx="{lx - 150}" cy="42" r="6" fill="{t["flagged"]}"/>')
    o.append(f'<text x="{lx - 138}" y="47" font-size="13" fill="{t["muted"]}">'
             f'flagged</text>')
    o.append(f'<circle cx="{lx - 62}" cy="42" r="6" fill="{t["real"]}"/>')
    o.append(f'<text x="{lx - 50}" y="47" font-size="13" fill="{t["muted"]}">'
             f'real</text>')

    for i, p in enumerate(PANELS):
        px = 40 + i * 450
        o.append(f'<line x1="{px}" y1="100" x2="{px + 380}" y2="100" '
                 f'stroke="{t["rule"]}" stroke-width="1"/>')
        o.append(f'<text x="{px}" y="126" font-size="14" font-weight="600" '
                 f'fill="{t["text"]}">{esc(p["title"])}</text>')

        scale = BAR_MAX / p["flagged"]
        for j, (key, colour) in enumerate((("flagged", t["flagged"]),
                                           ("real", t["real"]))):
            y = 146 + j * 40
            width = p[key] * scale
            o.append(bar(px, y, width, colour))
            o.append(f'<text x="{px + width + 10}" y="{y + 19}" font-size="16" '
                     f'font-weight="600" fill="{t["text"]}">{p[key]}</text>')

        o.append(f'<text x="{px}" y="256" font-size="13" fill="{t["muted"]}">'
                 f'{esc(p["note"])}</text>')
        o.append(f'<text x="{px}" y="278" font-size="13" font-weight="600" '
                 f'fill="{t["text"]}">{p["factor"]} overstated</text>')

    o.append("</g></svg>")
    return "\n".join(o)


if __name__ == "__main__":
    out = Path("assets")
    out.mkdir(exist_ok=True)
    for name in THEMES:
        path = out / f"overstatement-{name}.svg"
        path.write_text(build(name), encoding="utf-8")
        print(f"wrote {path}")
