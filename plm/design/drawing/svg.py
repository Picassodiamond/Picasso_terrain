"""Sheet -> SVG (millimetre user units, white paper) for the browser preview."""
from __future__ import annotations

from xml.sax.saxutils import escape

from .model import Circle, Hatch, Polyline, Sheet, Text, layer_info

_BASELINE = {"baseline": "alphabetic", "middle": "central", "top": "hanging", "bottom": "alphabetic"}
_ANCHOR = {"left": "start", "center": "middle", "right": "end"}


def sheet_to_svg(sheet: Sheet, *, background: str = "#ffffff", font: str = "Arial, Helvetica, sans-serif") -> str:
    W, H = sheet.paper
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:g} {H:g}" width="{W:g}mm" height="{H:g}mm" font-family="{font}" '
           f'data-kind="{sheet.kind}" data-index="{sheet.index}">',
           f'<title>{escape(sheet.title)}</title>', f'<rect x="0" y="0" width="{W:g}" height="{H:g}" fill="{background}"/>']
    patterns: dict[str, str] = {}
    body: list[str] = []
    for e in sheet.entities:
        if isinstance(e, Polyline):
            _, col, lw = layer_info(e.layer)
            lw = e.width or lw
            pts = " ".join(f"{x:.3f},{H - y:.3f}" for x, y in e.pts)
            tag = "polygon" if e.closed else "polyline"
            dash = ' stroke-dasharray="2 1.2"' if e.dashed else ""
            body.append(f'<{tag} points="{pts}" fill="none" stroke="{col}" stroke-width="{lw:g}" stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        elif isinstance(e, Text):
            _, col, _ = layer_info(e.layer)
            size = e.height * 1.38
            tr = f'translate({e.x:.3f},{H - e.y:.3f})' + (f' rotate({-e.rotation:.3f})' if e.rotation else "")
            weight = ' font-weight="bold"' if e.bold else ""
            body.append(f'<text transform="{tr}" font-size="{size:.3f}" fill="{col}" text-anchor="{_ANCHOR.get(e.align, "start")}" '
                        f'dominant-baseline="{_BASELINE.get(e.valign, "alphabetic")}"{weight}>{escape(e.text)}</text>')
        elif isinstance(e, Circle):
            _, col, lw = layer_info(e.layer)
            body.append(f'<circle cx="{e.x:.3f}" cy="{H - e.y:.3f}" r="{e.r:g}" fill="none" stroke="{col}" stroke-width="{lw:g}"/>')
        elif isinstance(e, Hatch):
            _, col, _ = layer_info(e.layer)
            pts = " ".join(f"{x:.3f},{H - y:.3f}" for x, y in e.pts)
            if e.pattern:
                pid = f"pat-{e.layer}-{e.pattern}".replace(" ", "")
                if pid not in patterns:
                    if e.pattern.upper() == "ANSI37":
                        lines = '<path d="M0,0 L2,2 M2,0 L0,2" stroke="{c}" stroke-width="0.12"/>'
                    else:
                        lines = '<path d="M0,2 L2,0" stroke="{c}" stroke-width="0.12"/>'
                    patterns[pid] = f'<pattern id="{pid}" patternUnits="userSpaceOnUse" width="2" height="2">{lines.format(c=col)}</pattern>'
                body.append(f'<polygon points="{pts}" fill="url(#{pid})" stroke="none"/>')
            else:
                body.append(f'<polygon points="{pts}" fill="{col}" fill-opacity="{e.opacity:g}" stroke="none"/>')
    if patterns:
        out.append("<defs>" + "".join(patterns.values()) + "</defs>")
    out.extend(body)
    out.append("</svg>")
    return "\n".join(out)
