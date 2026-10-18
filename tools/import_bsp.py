#!/usr/bin/env python3
"""Converts a Source 1 community hide-and-seek .bsp (VBSP 19/20) into a hide-n-seek map document.

    tools/import_bsp.py <map.bsp> <id> "<Name>" <author> <page-url> [--out=maps/<id>.json]

The approach is mg-prop-hunt-maps' importer, adapted to this repository's format and kept apart
from build_maps.py: the output is generated, committed, and never edited by hand. Run Python
that reads a downloaded file with `-I`.

What it does, and why each choice:

- The world's brushes become BOXES, because a map document has no meshes. Each brush's convex
  hull is found (bsp_read.Bsp.brush_hull) and its bounding box is written; a brush turned about
  the vertical axis becomes a turned box (a `yaw`), and one that is NOT a box (a roof slope, a
  stair) is cut into slabs along its longer horizontal axis, each as tall as the brush is at the
  slab's middle, so a ramp becomes a staircase of 0.53 m risers.
- Axes: the source is Z-up, godot = (x, z, -y). Scale: 1 unit = 0.01905 m (0.75 inch), which
  makes a 108-unit door 2.06 m. The author's ground is moved to y 0 (this game's floors stand at
  0 and `GROUND` sits 2 cm under).
- Textures are NOT carried: the author's materials are replaced with the game's own (Kenney's
  grid under pastels), by name, TEXTURE_MAP. Tool brushes (clip, trigger, hint, nodraw-only)
  are dropped; the sky box becomes the map's invisible wall.
- The author's player starts are shared out as hider and seeker spawns by their team, then
  settled by Map.build (moved off anything standing on them, faced).
- The source has no loose props, and a hide-and-seek map is judged by what a person hides
  behind, so cover is added by a seeded scatter (`scatter_cover`): street furniture and plants
  on the clearest open ground, spaced apart, the same every run.
- A ladder entity has no counterpart (the format has no ladders); see LADDERS in the printout.
"""
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bsp_read  # noqa: E402
import build_maps  # noqa: E402

S = 0.01905
SLAB = 28.0     # source units: ~0.53 m, a slope's riser

TOOLS = ("TOOLS/", "tools/")
TEXTURE_MAP = [
    ("CEILINGTILE", "ceiling", None),
    ("OFFCEILING", "concrete", None),
    ("CARPET", "carpet", None),
    ("BLINDS", "glass", None),
    ("LIGHTCOVER", "glass", (1.00, 1.00, 0.92)),
    ("OFFWNDW", "glass", None),
    ("TRUSS", "metal", None),
    ("DRTRIM", "metal", None),
    ("ELEVDR", "metal", None),
    ("PARTICLEBOARD", "wood", None),
    ("OFFDR", "wood", None),
    ("DESKTOP", "wood", None),
    ("WALLOFFICE", "paint", (0.92, 0.94, 1.00)),
    ("OFFWLLB", "plaster", (0.90, 0.95, 1.00)),
    ("OFFWLLC", "paint", (0.96, 0.94, 0.88)),
    ("BUILDING", "concrete", None),
    ("PLASTER", "plaster", (0.95, 0.92, 0.84)),
    ("GRASS", "grass", (0.85, 0.95, 0.75)),
    ("ACOUSTICCEILING", "ceiling", None),
    ("TILEFLOOR", "tile", None),
    ("WOODFLOOR", "parquet", None),
    ("TILEROOF", "roof", (1.00, 0.72, 0.62)),
    ("COMBINEGLASS", "glass", None),
    ("CONCRETE", "concrete", None),
    ("STONE", "stone", None),
    ("BRICK", "brick", None),
    ("WOOD", "wood", None),
    ("METAL", "metal", None),
    ("DEV/", "flat", (0.8, 0.8, 0.82)),
]

def classify(name):
    """A Source material name without its per-map folder and cubemap suffix. vbsp renames a
    material the map patches to MAPS/<map>/<group>/<name>_<x>_<y>_<z>, and the folder carries
    the map's own name, which must not decide a surface (a map called ph_school would match
    SCHOOL-anything); only the material's own name does."""
    n = name.upper()
    if n.startswith("MAPS/"):
        n = n.split("/", 2)[-1]
    return re.sub(r"(_-?\d+){3}$", "", n)


# Every entity class an author uses for a loose prop; the _dynamic ones are placed furniture
# that does not move in the original, which a catalogue prop is not either (it is a disguise).
PROP_CLASSES = ("prop_physics", "prop_physics_multiplayer", "prop_physics_override",
                "prop_dynamic", "prop_dynamic_override")
START_CLASSES = ("info_player_start", "info_player_terrorist", "info_player_counterterrorist",
                 "info_player_deathmatch", "info_player_teamspawn")


def within(b, x, z, slack=0.0):
    """Whether (x, z) is under box b's footprint, which a turned box (yaw) shares only in its own
    frame: the point is taken into it first (godot turns a box about +y)."""
    dx, dz = x - b["at"][0], z - b["at"][2]
    yaw = b.get("yaw", 0.0)
    if yaw:
        c, sn = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        dx, dz = dx * c - dz * sn, dx * sn + dz * c
    return abs(dx) <= b["size"][0] / 2 + slack and abs(dz) <= b["size"][2] / 2 + slack


def gd(p):
    return (p[0] * S, p[2] * S, -p[1] * S)


def kv(e, k, default=None):
    return e.get(k, default)


def vec(s):
    return [float(x) for x in s.split()]


def brush_material(bsp, index):
    first, count, _ = bsp.brushes[index]
    names = []
    for s in bsp.brushsides[first:first + count]:
        if s[3] or s[1] < 0:
            continue
        ti = bsp.texinfo[s[1]]
        td = int(ti[17])
        if 0 <= td < len(bsp.texdata):
            names.append(bsp.texnames[bsp.texdata[td][3]].upper())
    return names


def pick_material(names):
    real = [n for n in names if not n.startswith("TOOLS/")]
    if not real:
        return None
    # Majority of the sides, ignoring dev grids when anything else is there.
    pool = [n for n in real if not n.startswith("DEV/")] or real
    best = classify(max(set(pool), key=pool.count))
    for key, mat, tint in TEXTURE_MAP:
        if key in best:
            return mat, tint
    return "flat", None


def planes_of(bsp, index):
    first, count, _ = bsp.brushes[index]
    return [bsp.planes[s[0]][:4] for s in bsp.brushsides[first:first + count] if not s[3]]


def z_range(planes, x, y):
    lo, hi = -1e9, 1e9
    for nx, ny, nz, d in planes:
        if abs(nz) < 1e-6:
            continue
        z = (d - nx * x - ny * y) / nz
        if nz > 0:
            hi = min(hi, z)
        else:
            lo = max(lo, z)
    return lo, hi


def box_in_frame(pts, phi, tol):
    """The hull's extents in a frame turned phi degrees about z, or None when some corner is not
    a corner of that bounding box. (x0, x1), (y0, y1), (z0, z1), in the turned frame."""
    c, sn = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    q = [(p[0] * c + p[1] * sn, -p[0] * sn + p[1] * c, p[2]) for p in pts]
    x0, x1 = min(p[0] for p in q), max(p[0] for p in q)
    y0, y1 = min(p[1] for p in q), max(p[1] for p in q)
    z0, z1 = min(p[2] for p in q), max(p[2] for p in q)
    for p in q:
        if not ((abs(p[0] - x0) < tol or abs(p[0] - x1) < tol) and (abs(p[1] - y0) < tol or abs(p[1] - y1) < tol)
                and (abs(p[2] - z0) < tol or abs(p[2] - z1) < tol)):
            return None
    return (x0, x1), (y0, y1), (z0, z1)


def turned_box(bsp, index, pts):
    """(box, phi) when the brush is a box turned about the vertical axis, else None. The author's
    building is not always on the grid (a room at thirty degrees to the rest), and cutting such a
    wall into axis-aligned slabs gives a saw-tooth the players catch on. The angle is each upright
    side's direction modulo 90, tried in turn; the document's boxes carry a yaw."""
    first, count, _ = bsp.brushes[index]
    tried = set()
    for sd in bsp.brushsides[first:first + count]:
        if sd[3]:
            continue
        nx, ny, nz, _d = bsp.planes[sd[0]][:4]
        if abs(nz) > 1e-4:
            continue
        phi = round(math.degrees(math.atan2(ny, nx)) % 90.0, 2)
        if phi in tried or phi < 0.05 or phi > 89.95:
            continue
        tried.add(phi)
        # Four-decimal planes at 1000 units leave a unit of slack in the corners.
        box = box_in_frame(pts, phi, 1.5)
        if box is not None:
            return box, phi
    return None


def prism_box(pts):
    """(box, phi) for a brush that is a vertical prism of any plan shape: the smallest turned
    rectangle round its plan. A curved wall is a run of such wedges and a column of a round room
    is one; the box overhangs the wedge by a sliver, where cutting it into axis-aligned slabs (a
    slope's treatment, wrong for a plan shape) bit chunks out of the room. None for a brush whose
    top or bottom is not flat and level."""
    zs = sorted({round(p[2], 1) for p in pts})
    if len(zs) != 2:
        return None
    low = {(round(p[0], 1), round(p[1], 1)) for p in pts if abs(p[2] - zs[0]) < 0.15}
    high = {(round(p[0], 1), round(p[1], 1)) for p in pts if abs(p[2] - zs[1]) < 0.15}
    if low != high or len(low) < 3:
        return None
    plan = [(x, y, zs[0]) for x, y in low]
    best = None
    for (ax, ay), (bx_, by) in ((p, q) for p in low for q in low if p != q):
        phi = math.degrees(math.atan2(by - ay, bx_ - ax)) % 90.0
        box = box_in_frame(plan, phi, 1e9)
        area = (box[0][1] - box[0][0]) * (box[1][1] - box[1][0])
        if best is None or area < best[0] - 1e-6:
            best = (area, phi, box)
    _a, phi, box = best
    return (box[0], box[1], (zs[0], zs[1])), phi


def brush_boxes(bsp, index):
    """[((x range), (y range), (z range), phi)] in Source units, the ranges in a frame turned phi
    degrees (0 for most), and whether the brush had to be cut into slabs."""
    pts = bsp.brush_hull(index)
    if len(pts) < 4:
        return [], 0
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    z0, z1 = min(p[2] for p in pts), max(p[2] for p in pts)
    box_vol = (x1 - x0) * (y1 - y0) * (z1 - z0)
    # Hull volume from the corner points: a brush is a box when every corner is a box corner.
    corners = sum(1 for p in pts if (abs(p[0] - x0) < 0.5 or abs(p[0] - x1) < 0.5) and (abs(p[1] - y0) < 0.5 or abs(p[1] - y1) < 0.5) and (abs(p[2] - z0) < 0.5 or abs(p[2] - z1) < 0.5))
    if corners == len(pts):
        return [((x0, x1), (y0, y1), (z0, z1), 0.0)], 0
    tb = turned_box(bsp, index, pts)
    if tb is not None:
        return [tb[0] + (tb[1],)], 0
    pb = prism_box(pts)
    if pb is not None:
        return [pb[0] + (pb[1],)], 0
    pl = planes_of(bsp, index)
    out = []
    if x1 - x0 >= y1 - y0:
        n = max(1, math.ceil((x1 - x0) / SLAB))
        for i in range(n):
            a, b = x0 + (x1 - x0) * i / n, x0 + (x1 - x0) * (i + 1) / n
            lo, hi = z_range(pl, (a + b) / 2, (y0 + y1) / 2)
            lo, hi = max(lo, z0), min(hi, z1)
            if hi - lo > 0.5:
                out.append(((a, b), (y0, y1), (lo, hi), 0.0))
    else:
        n = max(1, math.ceil((y1 - y0) / SLAB))
        for i in range(n):
            a, b = y0 + (y1 - y0) * i / n, y0 + (y1 - y0) * (i + 1) / n
            lo, hi = z_range(pl, (x0 + x1) / 2, (a + b) / 2)
            lo, hi = max(lo, z0), min(hi, z1)
            if hi - lo > 0.5:
                out.append(((x0, x1), (a, b), (lo, hi), 0.0))
    return out, 1


def prune_far_scenery(m, starts, reach=12.0):
    """Drops the boxes of every cluster that is out of reach of the starts. A community map keeps
    its 3D skybox scenery (a distant city, a crane) in the same world as the play space, and the
    overview camera frames all of it, so the real map shrank to a corner of the frame. Boxes are
    grouped by touching (a quarter metre of slack); a group is kept when one of its boxes comes
    within `reach` metres of a start; the author's sky-box walls that bound the play space are
    big enough to pass, and a second, sealed sky cell holding the scenery is dropped with it."""
    if not starts:
        return
    n = len(m.boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def box_range(b):
        h = [b["size"][0] / 2, b["size"][1] / 2, b["size"][2] / 2]
        if b.get("yaw"):
            # The turned box's own bounds, so its slack is not the box's corner sticking out.
            c, sn = abs(math.cos(math.radians(b["yaw"]))), abs(math.sin(math.radians(b["yaw"])))
            h = [h[0] * c + h[2] * sn, h[1], h[0] * sn + h[2] * c]
        return [(b["at"][i] - h[i] - 0.25, b["at"][i] + h[i] + 0.25) for i in range(3)]

    rng = [box_range(b) for b in m.boxes]
    order = sorted(range(n), key=lambda i: rng[i][0][0])
    for ai, a in enumerate(order):
        for b in order[ai + 1:]:
            if rng[b][0][0] > rng[a][0][1]:
                break
            if all(rng[a][k][0] <= rng[b][k][1] and rng[b][k][0] <= rng[a][k][1] for k in (1, 2)):
                parent[find(a)] = find(b)
    keep = set()
    for i, b in enumerate(m.boxes):
        near = any(rng[i][0][0] - reach <= sx <= rng[i][0][1] + reach and rng[i][2][0] - reach <= sz <= rng[i][2][1] + reach
                   for sx, _sy, sz in starts)
        if near:
            keep.add(find(i))
    before = len(m.boxes)
    m.boxes[:] = [b for i, b in enumerate(m.boxes) if find(i) in keep]
    if len(m.boxes) != before:
        print("dropped %d boxes of scenery out of reach of the starts" % (before - len(m.boxes)))




# Street furniture and plants that a crouching person fits behind (0.8 m high, 0.6 m wide), from
# the game's catalogue.
COVER = ("nature_plant_bush_detailed", "furniture_bench", "nature_log_large", "nature_log_stack",
         "nature_plant_bush_detailed", "nature_fence_planks")


def scatter_cover(m, count, seed, spacing=5.0):
    """Cover on the clearest open ground, the same every run: a seeded shuffle of the open
    floor's best spots, each at least `spacing` metres from the last. Placed before the
    build so spawns are settled around it."""
    import random
    rng = random.Random(seed)
    spots = [s for s in m.spots(step=1.5, top=400) if s[0] >= 3.0]
    rng.shuffle(spots)
    placed = []
    for _size, x, z in spots:
        if len(placed) >= count:
            break
        if all((x - a) ** 2 + (z - b) ** 2 >= spacing ** 2 for a, b in placed):
            m.prop(rng.choice(COVER), x, z, yaw=rng.choice((0.0, 90.0, 180.0, 270.0)))
            placed.append((x, z))
    return len(placed)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    out_path = None
    for a in argv[1:]:
        if a.startswith("--out="):
            out_path = a[6:]
    bsp_path, mid, name, author, url = args[:5]
    bsp = bsp_read.Bsp(bsp_path).load()
    m = build_maps.Map(
        mid, name, "A city block of streets, courtyards and rooftops (a community map, by %s)." % author,
        {"sky_top": [0.32, 0.52, 0.82], "sky_horizon": [0.72, 0.82, 0.92], "ground": [0.35, 0.33, 0.30],
         "sun_pitch": -55, "sun_yaw": 30, "sun_energy": 1.1, "ambient": [0.62, 0.66, 0.72], "ambient_energy": 0.55},
        kill_y=-20.0)
    m.doc["author"] = author
    m.doc["source"] = url

    # The ground: the highest large flat top under the first start of either side.
    starts = [e for e in bsp.entities if e.get("classname") in START_CLASSES]
    lift = min(gd(vec(e["origin"]))[1] for e in starts)
    ground_y = None

    sliced = skipped = 0
    for bi in sorted(bsp.model_brushes(0)):
        names = brush_material(bsp, bi)
        mt = pick_material(names)
        invisible = False
        if mt is None:
            if any("TOOLSSKYBOX" in n for n in names):
                mt, invisible = ("flat", None), True
            else:
                skipped += 1
                continue
        boxes, was_sliced = brush_boxes(bsp, bi)
        sliced += was_sliced
        for (x0, x1), (y0, y1), (z0, z1), phi in boxes:
            if phi:
                if x1 - x0 < 0.05 or y1 - y0 < 0.05 or z1 - z0 < 0.05:
                    continue
                c, sn = math.cos(math.radians(phi)), math.sin(math.radians(phi))
                lx, ly = (x0 + x1) / 2, (y0 + y1) / 2
                gx, gy, gz = gd((lx * c - ly * sn, lx * sn + ly * c, (z0 + z1) / 2))
                bx = m.box((gx, gy, gz), ((x1 - x0) * S, (z1 - z0) * S, (y1 - y0) * S), mt[0], mt[1], yaw=phi)
            else:
                bx = m.span(x0 * S, x1 * S, z0 * S, z1 * S, -y1 * S, -y0 * S, mt[0], mt[1])
            if invisible and bx is not None:
                bx["visible"] = False
    print("brushes %d  tool-only %d  sliced %d  boxes %d" % (len(bsp.model_brushes(0)), skipped, sliced, len(m.boxes)))

    prune_far_scenery(m, [gd(vec(e["origin"])) for e in starts])

    # The author's ground is 96 units (the lowest start's floor); move it to y 0.
    ground = 96 * S
    for b in m.boxes:
        b["at"][1] = round(b["at"][1] - ground, 3)

    def support(x, z, ymax):
        best = 0.0 if ymax >= 0.0 else None
        for b in m.boxes:
            if b.get("solid", True) and within(b, x, z, 0.001):
                t = b["at"][1] + b["size"][1] / 2
                if t <= ymax and (best is None or t > best):
                    best = t
        return best

    # Terrorist starts hide, counter-terrorist starts seek: the team that starts higher up the
    # author's map is the one with somewhere to go.
    for e in starts:
        gx, gy, gz = gd(vec(e["origin"]))
        gy -= ground
        ang = vec(e.get("angles", "0 0 0"))
        floor = support(gx, gz, gy + 0.6)
        side = "hiders" if "terrorist" in e["classname"] and "counter" not in e["classname"] else "seekers"
        m.spawn(side, gx, gz, yaw=ang[1] - 90.0, y=(max(gy, 0.0) if floor is None else floor) + 0.05)

    for e in bsp.entities:
        if e.get("classname") in ("light", "light_spot"):
            gx, gy, gz = gd(vec(e["origin"]))
            c = vec(e.get("_light", "255 255 255 200"))
            m.light(gx, gy - ground, gz, rng=9.0, energy=min(1.2, c[3] / 200.0), colour=[c[0] / 255, c[1] / 255, c[2] / 255])

    ladders = [e for e in bsp.entities if e.get("classname") in ("info_ladder", "func_ladder")]
    print("LADDERS (no counterpart in the format, left out): %d" % len(ladders))

    n = scatter_cover(m, 28, seed=116073)
    print("cover %d  lights %d  spawns %d/%d" % (n, len(m.lights), len(m.spawns["hiders"]), len(m.spawns["seekers"])))
    doc = m.build()
    out_path = out_path or os.path.join(build_maps.ROOT, "maps", mid + ".json")
    with open(out_path, "w") as f:
        f.write(json.dumps(doc, indent=1) + "\n")
    print("wrote", out_path)


if __name__ == "__main__":
    main(sys.argv)
