"""DXF export """
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from ..contour import ContourLabel, ContourLine
from ..points import PointSet
from ..tin import TIN

# Legacy layer names and AutoCAD colour indices (from frm_main.Form_Load / pt_import)
LEGACY_LAYERS: dict[str, tuple[str, int]] = {
    "points": ("Points", 7),
    "points_blk": ("Points-Blk", 7),
    "points_num": ("Points-NUM", 4),
    "points_txt": ("Points-TXT", 3),
    "points_elv": ("Points-ELV", 6),
    "features": ("Features", 5),
    "boundary": ("Boundary", 6),
    "void": ("Void", 2),
    "triangle": ("Triangle", 11),
    "contour": ("Contour", 4),
    "index_contour": ("Index_Contour", 8),
    "cont_annotation": ("Cont_Annotation", 7),
    "h_align": ("H_ALIGN", 1),
    "horz_curve": ("HORZ_CURVE", 1),
    "chainage": ("Chainage", 7),
    "ipn": ("IPN", 7),
    "cross_section": ("Cross_Section", 7),
}


@dataclass
class DxfStyle:
    point_block: bool = True          # insert legacy POINTS block with PTNUM/DESC/ELEV attributes
    point_scale: float = 1.0
    text_height: float = 1.0          # contour label height
    contour_color_major: int | None = None   # None = ByLayer
    contour_color_minor: int | None = None
    arc_smoothing: bool = False       # write contour vertices with bulges (legacy 'round' look)
    arc_deflection_deg: float = 4.0   # legacy: only bends > 4 degrees get an arc
    version: str = "R2010"


def _ensure_layer(doc, name: str, color: int, linetype: str = "Continuous"):
    if name not in doc.layers:
        doc.layers.add(name, color=color, linetype=linetype)
    return doc.layers.get(name)


def _ensure_point_block(doc) -> None:
    """Legacy POINTS block: zero-length line + 3 attribute definitions (pt_import.CreatePointAttributeBlock)."""
    if "POINTS" in doc.blocks:
        return
    blk = doc.blocks.new(name="POINTS")
    blk.add_line((0, 0, 0), (0, 0, 0))
    specs = [
        ("PTNUM", "Point#", (-1.0, 0.25), 0.0, "CENTER", LEGACY_LAYERS["points_num"][0]),
        ("DESC", "Description", (-1.0, -1.25), 0.0, "CENTER", LEGACY_LAYERS["points_txt"][0]),
        ("ELEV", "Elevation", (0.0, 1.5), 45.0, "LEFT", LEGACY_LAYERS["points_elv"][0]),
    ]
    from ezdxf.enums import TextEntityAlignment

    for tag, prompt, ins, rot, halign, layer in specs:
        att = blk.add_attdef(
            tag, insert=ins, text=prompt, height=1.0, dxfattribs={"layer": layer, "rotation": rot}
        )
        att.dxf.prompt = prompt
        att.set_placement(
            ins,
            align=(
                TextEntityAlignment.BOTTOM_CENTER
                if halign == "CENTER"
                else TextEntityAlignment.BOTTOM_LEFT
            ),
        )


def _bulges_for(coords: np.ndarray, closed: bool, min_deflection_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Legacy 'round contour' idea: replace each bend by two vertices offset by T along the
    tangents with a bulge, T = e / tan(deflection/4), e taken as a small fraction of the shorter
    adjacent segment. Returns (xy (k,2), bulge (k,))."""
    c = np.asarray(coords, float)
    if closed and np.allclose(c[0], c[-1]):
        c = c[:-1]
    n = len(c)
    if n < 3:
        return c, np.zeros(len(c))
    out_pts: list[np.ndarray] = []
    out_blg: list[float] = []
    rng = range(n) if closed else range(1, n - 1)
    if not closed:
        out_pts.append(c[0])
        out_blg.append(0.0)
    for i in rng:
        p_prev, p, p_next = c[i - 1], c[i], c[(i + 1) % n]
        v1 = p - p_prev
        v2 = p_next - p
        l1, l2 = np.hypot(*v1), np.hypot(*v2)
        if l1 == 0 or l2 == 0:
            continue
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        dot = float(np.dot(v1, v2))
        defl = float(np.arctan2(abs(cross), dot))  # 0..pi
        if np.degrees(defl) <= min_deflection_deg or defl >= np.pi - 1e-6:
            out_pts.append(p)
            out_blg.append(0.0)
            continue
        T = 0.3 * min(l1, l2)
        a = p - v1 / l1 * T
        b = p + v2 / l2 * T
        bulge = float(np.tan(defl / 4.0)) * (1.0 if cross > 0 else -1.0)
        out_pts.append(a)
        out_blg.append(bulge)
        out_pts.append(b)
        out_blg.append(0.0)
    if not closed:
        out_pts.append(c[-1])
        out_blg.append(0.0)
    return np.asarray(out_pts), np.asarray(out_blg)


def write_dxf(
    path: str | Path,
    *,
    points: PointSet | None = None,
    tin: TIN | None = None,
    contours: Sequence[ContourLine] | None = None,
    contour_labels: Sequence[ContourLabel] | None = None,
    feature_lines: Sequence[np.ndarray] | None = None,
    boundary_lines: Sequence[np.ndarray] | None = None,
    extra_polylines: Sequence[tuple[str, np.ndarray, bool]] | None = None,  # (layer, coords, closed)
    extra_texts: Sequence[tuple[str, str, float, float, float, float]] | None = None,  # (layer, text, x, y, height, rot_deg)
    style: DxfStyle | None = None,
    layers: dict[str, tuple[str, int]] | None = None,
) -> Path:
    """Write a DXF that AutoCAD opens directly. Returns the written path."""
    import ezdxf

    st = style or DxfStyle()
    L = dict(LEGACY_LAYERS)
    if layers:
        L.update(layers)
    doc = ezdxf.new(st.version, setup=True)
    doc.header["$INSUNITS"] = 6  # metres
    msp = doc.modelspace()
    if "romans" not in doc.styles:
        doc.styles.add("romans", font="romans.shx")

    for key in ("points", "points_blk", "points_num", "points_txt", "points_elv", "features",
                "boundary", "triangle", "contour", "index_contour", "cont_annotation"):
        _ensure_layer(doc, *L[key])

    # -- points -------------------------------------------------------------------------
    if points is not None and len(points):
        if st.point_block:
            _ensure_point_block(doc)
        for i in range(len(points)):
            x, y, z = points.xyz[i]
            msp.add_point((x, y, z), dxfattribs={"layer": L["points"][0]})
            if st.point_block:
                ref = msp.add_blockref(
                    "POINTS", (x, y, z),
                    dxfattribs={"layer": L["points_blk"][0], "xscale": st.point_scale,
                                "yscale": st.point_scale, "zscale": st.point_scale},
                )
                ref.add_auto_attribs({
                    "PTNUM": points.ids[i] or str(i + 1),
                    "DESC": points.remarks[i],
                    "ELEV": f"{z:.3f}",
                })

    # -- lines --------------------------------------------------------------------------
    for coll, key in ((feature_lines, "features"), (boundary_lines, "boundary")):
        for c in coll or ():
            c = np.asarray(c, float)
            if c.shape[1] == 2:
                c = np.column_stack([c, np.zeros(len(c))])
            msp.add_polyline3d(c.tolist(), dxfattribs={"layer": L[key][0]})

    # -- TIN as 3DFACE -----------------------------------------------------------------
    if tin is not None:
        lay = L["triangle"][0]
        for t in tin.triangles:
            a, b, c = (tuple(tin.nodes[k]) for k in t)
            msp.add_3dface([a, b, c, c], dxfattribs={"layer": lay})

    # -- contours -----------------------------------------------------------------------
    for cl in contours or ():
        lay, col = (L["index_contour"] if cl.is_major else L["contour"])
        attribs = {"layer": lay, "elevation": cl.level}
        color = st.contour_color_major if cl.is_major else st.contour_color_minor
        if color is not None:
            attribs["color"] = color
        coords = cl.coords
        if st.arc_smoothing:
            xy, blg = _bulges_for(coords, cl.closed, st.arc_deflection_deg)
            pts = [(float(x), float(y), 0.0, 0.0, float(b)) for (x, y), b in zip(xy, blg)]
            pl = msp.add_lwpolyline(pts, format="xyseb", close=cl.closed, dxfattribs=attribs)
        else:
            if cl.closed and np.allclose(coords[0], coords[-1]):
                coords = coords[:-1]
            pl = msp.add_lwpolyline(coords.tolist(), format="xy", close=cl.closed, dxfattribs=attribs)
        del pl

    for lb in contour_labels or ():
        msp.add_text(
            lb.text,
            height=st.text_height,
            rotation=lb.angle_deg,
            dxfattribs={"layer": L["cont_annotation"][0], "style": "romans"},
        ).set_placement((lb.x, lb.y, lb.level), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    # -- extras (alignment, chainage, sections...) -------------------------------------
    for layer, coords, closed in extra_polylines or ():
        _ensure_layer(doc, layer, 7)
        c = np.asarray(coords, float)
        if c.shape[1] >= 3 and np.any(c[:, 2] != 0):
            msp.add_polyline3d(c[:, :3].tolist(), dxfattribs={"layer": layer}, close=closed)
        else:
            msp.add_lwpolyline(c[:, :2].tolist(), close=closed, dxfattribs={"layer": layer})
    for layer, text, x, y, h, rot in extra_texts or ():
        _ensure_layer(doc, layer, 7)
        msp.add_text(text, height=h, rotation=rot, dxfattribs={"layer": layer, "style": "romans"}).set_placement(
            (x, y), align=ezdxf.enums.TextEntityAlignment.LEFT
        )

    p = Path(path)
    doc.saveas(str(p))
    return p
