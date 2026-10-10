#!/usr/bin/env python3
"""Writes every map in maps/ from the plans below.

A map is a JSON document (see game-hide-n-seek's game/hs_map_doc.gd and maps/README.md), and
nothing stops a mapper writing one by hand. These are generated instead because a building is
hundreds of boxes that have to meet: a wall with a door in it is five boxes whose edges are
the door's, a room's floor, ceiling and light belong to the room, a stairwell is a dozen
slabs that must line up with the storeys, and a hedge maze is two hundred hedges. Here a wall
is one call with its holes, a room is one call, a flight of stairs is one call, and a maze is
one call with a seed.

    tools/build_maps.py                 # rewrite every map
    tools/build_maps.py --check         # fail if a file on disk is not what this would write
    tools/build_maps.py --sync-sizes    # copy prop sizes from ../game-hide-n-seek (when it is there)

Conventions, which every function below keeps:

- Metres, degrees. y is up and every floor's top is at y 0 unless a room says otherwise.
- A box's `at` is its centre. A prop's `at` is the middle of its footprint ON the floor.
- A prop's FRONT faces +Z at yaw 0 (Kenney's furniture does: a stove's door, a desk's drawers),
  so a sofa against a north wall (the wall at the smaller z) faces into the room at yaw 0, one
  against a south wall at yaw 180, against a west wall at 90 and an east wall at -90.
- Doors are 1.4 m wide and 2.3 m tall.
- Stairs are thin slabs, 0.22 m a step, so a flight has room under it for the flight below
  (see `flight`). A player steps 0.4 m.
- A crawl space is 1.1 m high: a person fits only crouched (0.95 m), so it is a hiding place a
  seeker has to get down to look into.
"""

import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIZES_FILE = os.path.join(HERE, "prop_sizes.json")
GAME_CATALOGUE = os.path.join(ROOT, "..", "game-hide-n-seek", "props", "catalogue.json")

WALL = 0.2          # an interior wall
OUTER = 0.3         # an exterior wall, brick outside and paint inside
DOOR_W = 1.4
DOOR_H = 2.3
SILL = 0.9          # a window's bottom
HEAD = 2.2          # a window's top


def r(v):
    return round(v, 3)


def v3(x, y, z):
    return [r(x), r(y), r(z)]


def load_sizes():
    with open(SIZES_FILE) as f:
        return json.load(f)["props"]


SIZES = load_sizes() if os.path.exists(SIZES_FILE) else {}


def size_of(pid):
    s = SIZES.get(pid)
    if s is None:
        raise SystemExit("unknown prop %s (run --sync-sizes?)" % pid)
    return s["size"]


def footprint(pid, yaw):
    """A prop's width (x) and depth (z) once turned by yaw (multiples of 90 only)."""
    sx, sy, sz = size_of(pid)
    if int(round(yaw)) % 180 == 90 or int(round(yaw)) % 180 == -90:
        return sz, sx
    return sx, sz


class Map:
    def __init__(self, mid, name, blurb, environment, round_seconds=0.0, hide_seconds=0.0, kill_y=-20.0):
        self.doc = {
            "format": 1, "kind": "hideseek_map", "id": mid, "name": name, "author": "hide-n-seek-maps",
            "blurb": blurb, "environment": environment, "kill_y": kill_y,
        }
        if round_seconds:
            self.doc["round_seconds"] = round_seconds
        if hide_seconds:
            self.doc["hide_seconds"] = hide_seconds
        self.boxes = []
        self.props = []
        self.lights = []
        self.spawns = {"hiders": [], "seekers": []}

    # --- Boxes -------------------------------------------------------------

    def box(self, at, size, mat, tint=None, yaw=0.0, solid=True, visible=True):
        b = {"at": v3(*at), "size": v3(*size), "mat": mat}
        if tint is not None:
            b["tint"] = [r(c) for c in tint]
        if yaw:
            b["yaw"] = r(yaw)
        if not solid:
            b["solid"] = False
        if not visible:
            b["visible"] = False
        self.boxes.append(b)
        return b

    def span(self, x0, x1, y0, y1, z0, z1, mat, tint=None, solid=True):
        """A box from its corners."""
        if x1 - x0 <= 0.001 or y1 - y0 <= 0.001 or z1 - z0 <= 0.001:
            return None
        return self.box(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), (x1 - x0, y1 - y0, z1 - z0), mat, tint, solid=solid)

    def floor(self, x0, z0, x1, z1, mat, tint=None, y=0.0, thick=0.2):
        return self.span(x0, x1, y - thick, y, z0, z1, mat, tint)

    def floor_around(self, x0, z0, x1, z1, hole, mat, tint=None, y=0.0, thick=0.2):
        """A floor with a rectangle (hx0, hz0, hx1, hz1) left out of it, as four boxes: a pool
        or a pond sunk into ground that would otherwise cover it."""
        hx0, hz0, hx1, hz1 = hole
        for a, b, c, d in ((x0, z0, x1, hz0), (x0, hz1, x1, z1), (x0, hz0, hx0, hz1), (hx1, hz0, x1, hz1)):
            self.floor(a, b, c, d, mat, tint, y, thick)

    def ceiling(self, x0, z0, x1, z1, height, mat="ceiling", tint=None, roof="roof", roof_tint=None, eave=0.3):
        self.span(x0, x1, height, height + 0.1, z0, z1, mat, tint)
        if roof:
            self.span(x0 - eave, x1 + eave, height + 0.1, height + 0.35, z0 - eave, z1 + eave, roof, roof_tint)

    def overlay(self, x0, z0, x1, z1, mat, tint=None, y=0.0):
        """Paint on a floor: a line, a path. Drawn a hair above it and solid to nothing."""
        return self.span(x0, x1, y + 0.002, y + 0.012, z0, z1, mat, tint, solid=False)

    def wall(self, a, b, along, mat, tint=None, height=3.2, y0=0.0, thick=WALL, holes=(), glass=True,
             inner=None, inner_tint=None):
        """A wall along x (along='x', at z=b[1]) or z (along='z', at x=b[0]) from a to b.

        a and b are (x, z). holes are (from, to, bottom, top) measured along the wall from a.
        A hole with bottom > 0 is a window and gets a pane of glass. `inner` is the material
        of a second, thinner layer on the room side (+z for an x wall, +x for a z wall); an
        exterior wall is brick outside and paint inside.
        """
        if along == "x":
            start, end = min(a[0], b[0]), max(a[0], b[0])
            fixed = a[1]
        else:
            start, end = min(a[1], b[1]), max(a[1], b[1])
            fixed = a[0]

        length = end - start
        cuts = sorted(holes, key=lambda h: h[0])
        layers = [(mat, tint, thick, 0.0)]
        if inner:
            layers = [(mat, tint, thick - 0.06, -0.03), (inner, inner_tint, 0.06, (thick - 0.06) / 2.0)]

        for lmat, ltint, lthick, shift in layers:
            pos = 0.0
            for h0, h1, hb, ht in cuts:
                self._wall_piece(along, start + pos, start + h0, fixed + shift, y0, y0 + height, lthick, lmat, ltint)
                if hb > 0.0:
                    self._wall_piece(along, start + h0, start + h1, fixed + shift, y0, y0 + hb, lthick, lmat, ltint)
                if ht < height:
                    self._wall_piece(along, start + h0, start + h1, fixed + shift, y0 + ht, y0 + height, lthick, lmat, ltint)
                pos = h1
            self._wall_piece(along, start + pos, start + length, fixed + shift, y0, y0 + height, lthick, lmat, ltint)

        if glass:
            for h0, h1, hb, ht in cuts:
                if hb > 0.0:
                    self._wall_piece(along, start + h0, start + h1, fixed, y0 + hb, y0 + ht, 0.04, "glass", None)

    def _wall_piece(self, along, p0, p1, fixed, y0, y1, thick, mat, tint):
        if p1 - p0 <= 0.001 or y1 - y0 <= 0.001:
            return
        if along == "x":
            self.span(p0, p1, y0, y1, fixed - thick / 2, fixed + thick / 2, mat, tint)
        else:
            self.span(fixed - thick / 2, fixed + thick / 2, y0, y1, p0, p1, mat, tint)

    # --- Props, lights, spawns ----------------------------------------------

    def prop(self, pid, x, z, yaw=0.0, y=0.0, pitch=0.0, roll=0.0):
        size_of(pid)
        p = {"id": pid, "at": v3(x, y, z)}
        if yaw:
            p["yaw"] = r(yaw)
        if pitch:
            p["pitch"] = r(pitch)
        if roll:
            p["roll"] = r(roll)
        self.props.append(p)
        return p

    def on(self, pid, under, dx=0.0, dz=0.0, yaw=0.0):
        """A prop standing on top of another one, `under` a prop dict this built."""
        top = under["at"][1] + size_of(under["id"])[1]
        return self.prop(pid, under["at"][0] + dx, under["at"][2] + dz, yaw, y=top)

    def against(self, pid, side, wall_at, along, gap=0.04, y=0.0):
        """A prop with its back to a wall. side is where the wall is: 'n' (smaller z), 's', 'w'
        (smaller x), 'e'. wall_at is the wall's inner face; along is the other coordinate."""
        yaw = {"n": 0.0, "s": 180.0, "w": 90.0, "e": -90.0}[side]
        w, d = footprint(pid, yaw)
        if side == "n":
            return self.prop(pid, along, wall_at + d / 2 + gap, yaw, y)
        if side == "s":
            return self.prop(pid, along, wall_at - d / 2 - gap, yaw, y)
        if side == "w":
            return self.prop(pid, wall_at + w / 2 + gap, along, yaw, y)
        return self.prop(pid, wall_at - w / 2 - gap, along, yaw, y)

    def light(self, x, y, z, rng=9.0, energy=1.0, colour=(1.0, 0.95, 0.85)):
        self.lights.append({"at": v3(x, y, z), "range": r(rng), "energy": r(energy), "colour": [r(c) for c in colour]})

    def spawn(self, side, x, z, yaw=0.0, y=0.05):
        self.spawns[side].append({"at": v3(x, y, z), "yaw": r(yaw)})

    def lamp_panel(self, x, z, height, w=1.2, d=0.4):
        """A ceiling light fitting: drawn, not solid, with the light under it."""
        self.span(x - w / 2, x + w / 2, height - 0.06, height, z - d / 2, z + d / 2, "flat", (2.6, 2.6, 2.4), solid=False)

    # --- Rooms ----------------------------------------------------------------

    def room(self, x0, z0, x1, z1, floor_mat, floor_tint=None, height=3.2, ceiling="ceiling", roof="roof",
             lights=1, energy=1.1, roof_tint=None, eave=0.3):
        """A floor, a ceiling over it with a roof above, and its lamps. Walls are separate.

        `eave` is how far the roof reaches past the room: over the walls, and out past the
        building. A room beside a taller one wants less, or its roof shows inside the other."""
        self.floor(x0, z0, x1, z1, floor_mat, floor_tint)
        if ceiling:
            self.ceiling(x0, z0, x1, z1, height, ceiling, None, roof, roof_tint, eave)
        cols = max(1, int(round((x1 - x0) / 8.0)))
        rows = max(1, int(round((z1 - z0) / 8.0)))
        if lights:
            for i in range(cols):
                for j in range(rows):
                    lx = x0 + (i + 0.5) * (x1 - x0) / cols
                    lz = z0 + (j + 0.5) * (z1 - z0) / rows
                    self.lamp_panel(lx, lz, height)
                    self.light(lx, height - 0.5, lz, rng=max(x1 - x0, z1 - z0) / max(cols, rows) * 1.25 + 3.0, energy=energy)

    # --- Spawns ----------------------------------------------------------------

    PERSON = 0.45       # a person's radius and a little, for "is this spawn clear"
    PERSON_H = 1.9

    def _blocked(self, x, y, z):
        """What a person standing at (x, y, z) would be inside, or None. Props by their turned
        footprint, solid boxes by their extent; both as axis-aligned rectangles, which is
        generous for a turned one and so errs towards moving a spawn that was fine."""
        lo, hi = y + 0.1, y + self.PERSON_H
        for p in self.props:
            sx, sy, sz = size_of(p["id"])
            yaw = math.radians(p.get("yaw", 0.0))
            w = abs(sx * math.cos(yaw)) + abs(sz * math.sin(yaw))
            d = abs(sx * math.sin(yaw)) + abs(sz * math.cos(yaw))
            py = p["at"][1]
            if py + sy < lo or py > hi:
                continue
            if abs(x - p["at"][0]) < w / 2 + self.PERSON and abs(z - p["at"][2]) < d / 2 + self.PERSON:
                return p["id"]
        for b in self.boxes:
            if b.get("solid", True) is False:
                continue
            (bx, by, bz), (sx, sy, sz) = b["at"], b["size"]
            if by + sy / 2 < lo or by - sy / 2 > hi:
                continue
            if b.get("yaw"):
                yaw = math.radians(b["yaw"])
                sx, sz = abs(sx * math.cos(yaw)) + abs(sz * math.sin(yaw)), abs(sx * math.sin(yaw)) + abs(sz * math.cos(yaw))
            if abs(x - bx) < sx / 2 + self.PERSON and abs(z - bz) < sz / 2 + self.PERSON:
                return "a %s box" % b["mat"]
        return None

    def _floored(self, x, y, z):
        for b in self.boxes:
            if b.get("solid", True) is False:
                continue
            (bx, by, bz), (sx, sy, sz) = b["at"], b["size"]
            if abs(by + sy / 2 - y) < 0.12 and abs(x - bx) < sx / 2 - 0.1 and abs(z - bz) < sz / 2 - 0.1:
                return True
        return False

    def _settle_spawns(self):
        """Moves every spawn that a prop or a wall stands on to the nearest clear spot.

        A spawn is written where a room's plan says the players start, before the room is
        furnished, and a sofa put down later lands on it: mg-prop-hunt's headless_maps found
        six (a living room's corner sofa, an office desk, a hallway bench). Moving them here
        rather than by hand keeps a re-furnished room right without anybody remembering to."""
        for side, spots in self.spawns.items():
            for i, s in enumerate(spots):
                x, y, z = s["at"]
                what = self._blocked(x, y, z)
                if what is None:
                    continue
                found = None
                for ring in range(1, 9):
                    step = ring * 0.5
                    for k in range(8 * ring):
                        a = 2.0 * math.pi * k / (8 * ring)
                        cx, cz = x + step * math.cos(a), z + step * math.sin(a)
                        if self._blocked(cx, y, cz) is None and self._floored(cx, y, cz):
                            found = (cx, cz)
                            break
                    if found:
                        break
                if found is None:
                    raise SystemExit("%s: %s spawn %d at %s is inside %s and nothing near it is clear" % (self.doc["id"], side, i, s["at"], what))
                s["at"] = v3(found[0], y, found[1])
                print("  %s: %s spawn %d moved off %s to %s" % (self.doc["id"], side, i, what, s["at"]), file=sys.stderr)

    def _face_spawns(self):
        """Turns every spawn towards the middle of the map's furniture.

        The game's yaw 0 looks along -Z and its formula is yaw = atan2(-dx, -dz); the
        hand-written yaws assumed the other way round, and a browser render found a hunter
        released on Willow Lane's street looking at empty sky. Every map but the school had
        most of its spawns facing away. Computed rather than written per spawn, so a moved
        spawn or a re-furnished room cannot point the wrong way again."""
        if not self.props:
            return
        cx = sum(p["at"][0] for p in self.props) / len(self.props)
        cz = sum(p["at"][2] for p in self.props) / len(self.props)
        for spots in self.spawns.values():
            for s in spots:
                dx, dz = cx - s["at"][0], cz - s["at"][2]
                if abs(dx) + abs(dz) > 0.01:
                    s["yaw"] = r(math.degrees(math.atan2(-dx, -dz)))

    def build(self):
        self._settle_spawns()
        self._face_spawns()
        self.doc["boxes"] = self.boxes
        self.doc["props"] = self.props
        self.doc["lights"] = self.lights
        self.doc["spawns"] = self.spawns
        return self.doc


# --- Furnishing ------------------------------------------------------------------

def bathroom(m, x0, z0, x1, z1, stalls=2):
    """Stalls with toilets along the back, sinks along the side."""
    stall_w = 1.5
    for i in range(stalls):
        sx = x0 + 0.2 + i * stall_w
        m.prop("furniture_toilet", sx + stall_w / 2, z1 - 0.6, 180.0)
        if i > 0:
            m.span(sx - 0.03, sx + 0.03, 0.15, 2.0, z1 - 1.7, z1, "metal", (0.75, 0.82, 0.88))
    m.span(x0 + 0.2 + stalls * stall_w - 0.03, x0 + 0.2 + stalls * stall_w + 0.03, 0.15, 2.0, z1 - 1.7, z1, "metal", (0.75, 0.82, 0.88))
    for i in range(2):
        m.against("furniture_bathroom_sink_square", "e", x1, z0 + 1.0 + i * 1.2)
    m.prop("furniture_trashcan", x1 - 0.4, z0 + 3.4)
    m.against("furniture_bathroom_cabinet", "w", x0, z0 + 0.8)


def lockers(m, x0, x1, z, side, height=2.0):
    """A row of lockers against a corridor wall: part of the wall, not something to hide as."""
    depth = 0.45
    if side == "n":
        m.span(x0, x1, 0.0, height, z, z + depth, "locker")
    else:
        m.span(x0, x1, 0.0, height, z - depth, z, "locker")


def tree_ring(m, x0, z0, x1, z1, step, kinds, seed):
    """Trees along the edge of an area, a little irregular."""
    i = 0
    for x in frange(x0, x1, step):
        for z in (z0, z1):
            kind = kinds[(i * 7 + seed) % len(kinds)]
            m.prop(kind, x + ((i * 37) % 5 - 2) * 0.3, z + ((i * 53) % 5 - 2) * 0.3, (i * 47) % 360)
            i += 1
    for z in frange(z0 + step, z1 - step + 0.01, step):
        for x in (x0, x1):
            kind = kinds[(i * 7 + seed) % len(kinds)]
            m.prop(kind, x + ((i * 37) % 5 - 2) * 0.3, z + ((i * 53) % 5 - 2) * 0.3, (i * 47) % 360)
            i += 1


def frange(a, b, step):
    out = []
    v = a
    while v <= b + 1e-6:
        out.append(v)
        v += step
    return out


def fence(m, x0, z0, x1, z1, height=1.6, mat="wood", tint=(0.62, 0.48, 0.34), gaps=()):
    """A fence round an area, as thin walls with gates (gaps: (side, from, to))."""
    sides = {
        "n": ((x0, z0), (x1, z0), "x"),
        "s": ((x0, z1), (x1, z1), "x"),
        "w": ((x0, z0), (x0, z1), "z"),
        "e": ((x1, z0), (x1, z1), "z"),
    }
    for side, (a, b, along) in sides.items():
        holes = [(g0, g1, 0.0, height) for gs, g0, g1 in gaps if gs == side]
        m.wall(a, b, along, mat, tint, height=height, thick=0.12, holes=holes, glass=False)


# --- What hide-and-seek adds ----------------------------------------------------------
#
# A hide-and-seek map is judged by three things the genre's best maps all have: more than one
# way round (a chase that reaches a dead end is over), somewhere UP (a roof, a loft, a gallery,
# a rack), and somewhere a seeker has to get down on their knees to look (a crawl space, a
# rack's bottom shelf, the space under a bridge). Every map below has all three.

STEP = 0.22         # a stair's rise; a player steps 0.4
CRAWL_H = 1.1       # a crawl space's height; a person crouched is 0.95


def room_at(m, x0, z0, x1, z1, y, floor_mat, floor_tint=None, height=3.2, ceiling="ceiling", roof="roof",
            lights=1, energy=1.1, roof_tint=None, eave=0.3, floor=True):
    """`Map.room` for a room whose floor is at `y`: an upper storey, a mezzanine, a loft.
    `floor=False` when the slab under it is laid separately (a storey with a stair hole)."""
    if floor:
        m.floor(x0, z0, x1, z1, floor_mat, floor_tint, y=y)
    if ceiling:
        m.ceiling(x0, z0, x1, z1, y + height, ceiling, None, roof, roof_tint, eave)
    cols = max(1, int(round((x1 - x0) / 8.0)))
    rows = max(1, int(round((z1 - z0) / 8.0)))
    if lights:
        for i in range(cols):
            for j in range(rows):
                lx = x0 + (i + 0.5) * (x1 - x0) / cols
                lz = z0 + (j + 0.5) * (z1 - z0) / rows
                m.lamp_panel(lx, lz, y + height)
                m.light(lx, y + height - 0.5, lz, rng=max(x1 - x0, z1 - z0) / max(cols, rows) * 1.25 + 3.0, energy=energy)


def flight(m, x0, x1, z0, z1, y0, y1, ascend, mat="wood", tint=None, thick=0.3):
    """A straight flight of stairs filling x0..x1, z0..z1, climbing from y0 to y1 in the
    direction `ascend` ('n' towards smaller z, 's', 'w', 'e'). Each step is a slab `thick`
    deep rather than a block to the floor, so the space under a flight is room: for the flight
    below it in a stairwell, or for somebody hiding under the stairs."""
    rise = y1 - y0
    n = max(1, int(math.ceil(rise / STEP)))
    for i in range(n):
        top = y0 + rise * (i + 1) / n
        bottom = max(y0, top - thick)
        f0, f1 = i / n, (i + 1) / n
        if ascend == "s":
            m.span(x0, x1, bottom, top, z0 + (z1 - z0) * f0, z0 + (z1 - z0) * f1, mat, tint)
        elif ascend == "n":
            m.span(x0, x1, bottom, top, z1 - (z1 - z0) * f1, z1 - (z1 - z0) * f0, mat, tint)
        elif ascend == "e":
            m.span(x0 + (x1 - x0) * f0, x0 + (x1 - x0) * f1, bottom, top, z0, z1, mat, tint)
        else:
            m.span(x1 - (x1 - x0) * f1, x1 - (x1 - x0) * f0, bottom, top, z0, z1, mat, tint)


def railing(m, x0, z0, x1, z1, y, height=1.0, mat="metal", tint=(0.35, 0.37, 0.40)):
    """A rail along one edge (a line, so x0 == x1 or z0 == z1): a top bar and posts, solid, so
    nobody walks off a gallery but a seeker below can see through it."""
    m.span(min(x0, x1) - 0.04, max(x0, x1) + 0.04, y + height - 0.06, y + height, min(z0, z1) - 0.04, max(z0, z1) + 0.04, mat, tint)
    m.span(min(x0, x1) - 0.04, max(x0, x1) + 0.04, y + 0.45, y + 0.5, min(z0, z1) - 0.04, max(z0, z1) + 0.04, mat, tint)
    length = max(abs(x1 - x0), abs(z1 - z0))
    posts = max(1, int(length / 1.5))
    for i in range(posts + 1):
        t = i / posts
        px, pz = x0 + (x1 - x0) * t, z0 + (z1 - z0) * t
        m.span(px - 0.04, px + 0.04, y, y + height, pz - 0.04, pz + 0.04, mat, tint)


def crawl(m, x0, z0, x1, z1, along, y=0.0, height=CRAWL_H, mat="metal", tint=None):
    """A tunnel open at both ends that a person fits through only crouched: a vent, a duct, a
    play tube. `along` is the axis it runs on."""
    t = 0.12
    if along == "x":
        m.span(x0, x1, y, y + height + t, z0 - t, z0, mat, tint)
        m.span(x0, x1, y, y + height + t, z1, z1 + t, mat, tint)
        m.span(x0, x1, y + height, y + height + t, z0, z1, mat, tint)
    else:
        m.span(x0 - t, x0, y, y + height + t, z0, z1, mat, tint)
        m.span(x1, x1 + t, y, y + height + t, z0, z1, mat, tint)
        m.span(x0, x1, y + height, y + height + t, z0, z1, mat, tint)


def rack(m, x0, x1, z, seed, depth=1.15, shelves=(1.35, 2.55), height=3.6, tint=(0.25, 0.42, 0.72)):
    """Warehouse shelving along x, centred on z: uprights every bay, a pallet deck, two shelves.

    The deck's top is 0.15 and the first shelf's underside is 1.27, so the bottom of every bay is
    a crawl space a hider can get into behind the boxes, and a seeker has to crouch to see in."""
    zn, zs = z - depth / 2, z + depth / 2
    bays = max(1, int(round((x1 - x0) / 2.6)))
    for i in range(bays + 1):
        bx = x0 + (x1 - x0) * i / bays
        for pz in (zn, zs):
            m.span(bx - 0.06, bx + 0.06, 0.0, height, pz - 0.06, pz + 0.06, "metal", tint)
    m.span(x0, x1, 0.0, 0.15, zn, zs, "wood", (0.72, 0.6, 0.45))
    for s in shelves:
        m.span(x0 - 0.06, x1 + 0.06, s - 0.08, s, zn - 0.06, zs + 0.06, "metal", (0.82, 0.48, 0.18))
    # Boxes: on the shelves, and on the deck in some bays only, so some bays are a way in.
    k = seed
    for i in range(bays):
        cx = x0 + (x1 - x0) * (i + 0.5) / bays
        k = (k * 1103515245 + 12345) % 2147483648
        if k % 3 == 0:
            m.prop("furniture_cardboard_box_closed", cx - 0.5, z - 0.2, (k % 40) - 20, y=0.15)
            m.prop("furniture_cardboard_box_open", cx + 0.45, z + 0.1, (k % 30) - 15, y=0.15)
        for s in shelves:
            k = (k * 1103515245 + 12345) % 2147483648
            n = k % 3 + 1
            for j in range(n):
                pid = "furniture_cardboard_box_closed" if (k >> j) % 2 else "furniture_cardboard_box_open"
                m.prop(pid, cx - 0.8 + j * 0.85, z + ((k >> (j + 3)) % 3 - 1) * 0.15, ((k >> j) % 20) - 10, y=s)


def container(m, x0, z0, along, y=0.0, open_end=None, tint=(0.62, 0.22, 0.18), length=6.0, width=2.4, height=2.6):
    """A shipping container with its near corner at (x0, z0) running `along` x or z, solid
    everywhere except `open_end` ('w'/'e' for one along x, 'n'/'s' along z), which is open:
    a room you can hide at the back of. Its roof is walkable."""
    t = 0.1
    x1 = x0 + (length if along == "x" else width)
    z1 = z0 + (width if along == "x" else length)
    m.span(x0, x1, y, y + 0.15, z0, z1, "metal", tint)
    m.span(x0, x1, y + height - t, y + height, z0, z1, "metal", tint)
    if along == "x":
        m.span(x0, x1, y + 0.15, y + height - t, z0, z0 + t, "metal", tint)
        m.span(x0, x1, y + 0.15, y + height - t, z1 - t, z1, "metal", tint)
        if open_end != "w":
            m.span(x0, x0 + t, y + 0.15, y + height - t, z0 + t, z1 - t, "metal", tint)
        if open_end != "e":
            m.span(x1 - t, x1, y + 0.15, y + height - t, z0 + t, z1 - t, "metal", tint)
    else:
        m.span(x0, x0 + t, y + 0.15, y + height - t, z0, z1, "metal", tint)
        m.span(x1 - t, x1, y + 0.15, y + height - t, z0, z1, "metal", tint)
        if open_end != "n":
            m.span(x0 + t, x1 - t, y + 0.15, y + height - t, z0, z0 + t, "metal", tint)
        if open_end != "s":
            m.span(x0 + t, x1 - t, y + 0.15, y + height - t, z1 - t, z1, "metal", tint)
    return (x0, z0, x1, z1)


def car(m, x, z, along, tint):
    """A parked car: a body and a cabin, solid, a little over a metre high. Crouch behind it."""
    if along == "x":
        m.span(x - 2.1, x + 2.1, 0.2, 1.0, z - 0.85, z + 0.85, "metal", tint)
        m.span(x - 1.0, x + 0.9, 1.0, 1.5, z - 0.75, z + 0.75, "glass", None)
    else:
        m.span(x - 0.85, x + 0.85, 0.2, 1.0, z - 2.1, z + 2.1, "metal", tint)
        m.span(x - 0.75, x + 0.75, 1.0, 1.5, z - 1.0, z + 0.9, "glass", None)


def stall(m, x, z, cloth):
    """A market stall: a counter, four posts, a cloth roof, boxes under the counter."""
    m.span(x - 1.3, x + 1.3, 0.0, 0.95, z - 0.5, z + 0.5, "wood", (0.66, 0.5, 0.34))
    for dx in (-1.35, 1.35):
        for dz in (-0.55, 1.6):
            m.span(x + dx - 0.06, x + dx + 0.06, 0.0, 2.4, z + dz - 0.06, z + dz + 0.06, "wood", (0.55, 0.4, 0.28))
    m.span(x - 1.55, x + 1.55, 2.4, 2.5, z - 0.75, z + 1.8, "fabric", cloth)
    m.prop("furniture_cardboard_box_open", x - 0.6, z + 1.0, 10.0)
    m.prop("furniture_cardboard_box_closed", x + 0.7, z + 1.1, -15.0)


def hedge_maze(m, x0, z0, cols, rows, cell, seed, height=2.3, gate_w=1, gate_e=None):
    """A maze of hedges, cols x rows cells of `cell` metres, carved by a seeded depth-first walk
    so it is the same maze every build. Entrances on the west side at row `gate_w` and on the
    east at `gate_e`. Dead ends are the point: a hider who runs into one has to be quick."""
    rng = random.Random(seed)
    seen = [[False] * rows for _ in range(cols)]
    # Walls: east of (c, r), and south of (c, r).
    east = [[True] * rows for _ in range(cols)]
    south = [[True] * rows for _ in range(cols)]
    stack = [(0, 0)]
    seen[0][0] = True
    while stack:
        c, r = stack[-1]
        options = []
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and 0 <= nr < rows and not seen[nc][nr]:
                options.append((nc, nr, dc, dr))
        if not options:
            stack.pop()
            continue
        nc, nr, dc, dr = rng.choice(options)
        if dc == 1:
            east[c][r] = False
        elif dc == -1:
            east[nc][nr] = False
        elif dr == 1:
            south[c][r] = False
        else:
            south[nc][nr] = False
        seen[nc][nr] = True
        stack.append((nc, nr))
    t = 0.6
    tint = (0.85, 1.0, 0.85)
    x1, z1 = x0 + cols * cell, z0 + rows * cell
    # The outside, with its gates.
    for r in range(rows):
        if r != gate_w:
            m.span(x0 - t / 2, x0 + t / 2, 0.0, height, z0 + r * cell - t / 2, z0 + (r + 1) * cell + t / 2, "hedge", tint)
        if r != gate_e:
            m.span(x1 - t / 2, x1 + t / 2, 0.0, height, z0 + r * cell - t / 2, z0 + (r + 1) * cell + t / 2, "hedge", tint)
    m.span(x0 - t / 2, x1 + t / 2, 0.0, height, z0 - t / 2, z0 + t / 2, "hedge", tint)
    m.span(x0 - t / 2, x1 + t / 2, 0.0, height, z1 - t / 2, z1 + t / 2, "hedge", tint)
    for c in range(cols):
        for r in range(rows):
            cx, cz = x0 + (c + 1) * cell, z0 + (r + 1) * cell
            if east[c][r] and c < cols - 1:
                m.span(cx - t / 2, cx + t / 2, 0.0, height, cz - cell - t / 2, cz + t / 2, "hedge", tint)
            if south[c][r] and r < rows - 1:
                m.span(cx - cell - t / 2, cx + t / 2, 0.0, height, cz - t / 2, cz + t / 2, "hedge", tint)


def boundary(m, x0, z0, x1, z1, height=3.0, mat="brick", tint=None):
    """The edge of the map: four walls nobody climbs."""
    t = 0.4
    m.span(x0 - t, x1 + t, 0.0, height, z0 - t, z0, mat, tint)
    m.span(x0 - t, x1 + t, 0.0, height, z1, z1 + t, mat, tint)
    m.span(x0 - t, x0, 0.0, height, z0, z1, mat, tint)
    m.span(x1, x1 + t, 0.0, height, z0, z1, mat, tint)


# --- Furnishing a home -----------------------------------------------------------------------

def living_room(m, x0, z0, x1, z1, y=0.0):
    """A sofa against the north wall facing a television on the south, a rug, a chair, a lamp,
    a bookcase and a plant: the room every hider checks the back of the sofa in."""
    cx = (x0 + x1) / 2
    m.against("furniture_lounge_sofa", "n", z0, cx, y=y)
    m.prop("furniture_rug_rectangle", cx, (z0 + z1) / 2, y=y)
    m.prop("furniture_table_coffee", cx, z0 + 2.0, y=y)
    m.against("furniture_cabinet_television", "s", z1, cx, y=y)
    m.prop("furniture_television_modern", cx, z1 - 0.35, 180.0, y=y + 0.71)
    m.against("furniture_lounge_chair", "w", x0, z1 - 1.0, y=y)
    m.against("furniture_bookcase_closed_doors", "e", x1, z0 + 1.0, y=y)
    m.prop("furniture_potted_plant", x1 - 0.45, z1 - 0.45, y=y)
    m.prop("furniture_lamp_round_floor", x0 + 0.35, z0 + 0.35, y=y)


def bedroom(m, x0, z0, x1, z1, y=0.0, double=True):
    """A bed against the north wall, a wardrobe (a tall closed bookcase), a desk and a chest."""
    cx = (x0 + x1) / 2
    m.against("furniture_bed_double" if double else "furniture_bed_single", "n", z0, cx, y=y)
    m.against("furniture_cabinet_bed_drawer_table", "n", z0, cx - (1.5 if double else 1.0), y=y)
    m.against("furniture_bookcase_closed_wide", "e", x1, z1 - 1.2, y=y)
    m.against("furniture_desk", "w", x0, z1 - 1.2, y=y)
    m.prop("furniture_chair_desk", x0 + 1.3, z1 - 1.2, -90.0, y=y)
    m.prop("furniture_rug_round", cx, (z0 + z1) / 2 + 0.6, y=y)


def kitchen_row(m, x0, x1, z, side, y=0.0):
    """Units against a wall from x0 to x1: fridge, sink, stove, cabinets, with the small things on top."""
    pids = ["furniture_kitchen_fridge", "furniture_kitchen_cabinet", "furniture_kitchen_sink",
            "furniture_kitchen_stove", "furniture_kitchen_cabinet_drawer", "furniture_kitchen_cabinet"]
    x = x0 + 0.55
    placed = []
    for pid in pids:
        if x + 0.5 > x1:
            break
        placed.append(m.against(pid, side, z, x, y=y))
        x += 1.0
    for p in placed:
        if p["id"] == "furniture_kitchen_cabinet":
            m.on("furniture_kitchen_microwave", p)
            break


def bath(m, x0, z0, x1, z1, y=0.0):
    """A toilet, a sink and a shower: the room nobody thinks a hider would pick."""
    m.against("furniture_toilet", "n", z0, x0 + 0.6, y=y)
    m.against("furniture_bathroom_sink", "n", z0, x0 + 1.6, y=y)
    m.prop("furniture_shower", x1 - 0.75, z1 - 0.75, y=y)


# --- The maps ----------------------------------------------------------------------------------

def apartments():
    """Larkspur Court: a three-storey block of flats, its roof, a fire escape, an alley, a back
    yard, a corner shop and the street.

    The climb is the map. A switchback stairwell runs from the ground to a hut on the roof; a
    fire escape on the west face reaches the first and second floors from the alley; and every
    floor's corridor joins the two. So a hider seen on the stairs can go up, along and down the
    outside, and a seeker who follows has to choose."""
    m = Map(
        "hs_apartments", "Larkspur Court",
        "Three floors of flats, a roof, a fire escape and the street. Up is a way out.",
        {"sky_top": [0.40, 0.50, 0.64], "sky_horizon": [0.74, 0.78, 0.82], "ground": [0.32, 0.32, 0.33],
         "sun_pitch": -48, "sun_yaw": 140, "sun_energy": 0.9, "sun_colour": [1.0, 0.95, 0.88],
         "ambient": [0.62, 0.64, 0.68], "ambient_energy": 0.45, "fog": 0.002},
        round_seconds=0, hide_seconds=0, kill_y=-10.0,
    )
    X0, X1, Z0, Z1 = -16.0, 10.0, -12.0, 6.0
    Y = [0.0, 3.4, 6.8]
    TOP = 10.2
    H = 3.2
    plaster = (0.92, 0.90, 0.84)

    # The lot: concrete, the street, the boundary.
    m.floor(-32, -28, 24, 24, "concrete", (0.82, 0.82, 0.8))
    m.overlay(-32, 11, 24, 19, "asphalt")
    for x in frange(-30, 22, 4.0):
        m.overlay(x, 14.9, x + 2.0, 15.1, "line", (1.0, 0.95, 0.6))
    m.overlay(-32, 8, 24, 11, "sidewalk")
    m.overlay(-32, 19, 24, 21, "sidewalk")
    boundary(m, -32, -28, 24, 24, height=4.0)

    # --- The building -------------------------------------------------------
    hole = (6.0, -11.7, 10.0, -2.0)            # the stairwell, open from the ground to the roof
    for k, y in enumerate(Y + [TOP]):
        if k == 0:
            m.floor(X0, Z0, X1, Z1, "tile", (0.85, 0.82, 0.78), y=0.0)
            continue
        # A slab with the stairwell left out: four pieces round the hole.
        m.span(X0, hole[0], y - 0.2, y, Z0, Z1, "carpet" if k < 3 else "roof", (0.75, 0.68, 0.6) if k < 3 else None)
        m.span(hole[0], X1, y - 0.2, y, hole[3], Z1, "carpet" if k < 3 else "roof", (0.75, 0.68, 0.6) if k < 3 else None)
        m.span(hole[0], X1, y - 0.2, y, Z0, hole[1], "concrete")

    for k, y in enumerate(Y):
        # Outer walls: brick outside, plaster inside, windows on every face, the doors below.
        south_holes = [(1.0, 2.8, SILL, HEAD), (6.5, 8.3, SILL, HEAD), (12.0, 13.8, SILL, HEAD), (18.5, 20.3, SILL, HEAD)]
        north_holes = [(2.0, 3.8, SILL, HEAD), (7.5, 9.3, SILL, HEAD), (13.5, 15.3, SILL, HEAD)]
        west_holes = [(5.0, 6.4, SILL, HEAD), (11.5, 12.9, SILL, HEAD)]
        east_holes = [(13.0, 14.8, SILL, HEAD)]
        if k == 0:
            south_holes = [(1.0, 2.8, SILL, HEAD), (9.5, 11.5, 0.0, DOOR_H), (18.5, 20.3, SILL, HEAD)]
            north_holes = [(1.0, 2.4, 0.0, DOOR_H), (13.5, 15.3, SILL, HEAD)]
            west_holes = [(5.0, 6.4, SILL, HEAD)]
            east_holes = [(11.2, 12.6, 0.0, DOOR_H)]
        if k == 1:
            west_holes = [(7.0, 8.4, 0.0, DOOR_H), (11.5, 12.9, SILL, HEAD)]     # to the fire escape
        if k == 2:
            west_holes = [(5.0, 6.4, SILL, HEAD), (15.8, 17.0, 0.0, DOOR_H)]     # to the fire escape
        m.wall((X0, Z1), (X1, Z1), "x", "brick", None, height=H, y0=y, thick=OUTER, holes=south_holes, inner="plaster", inner_tint=plaster)
        m.wall((X0, Z0), (X1, Z0), "x", "brick", None, height=H, y0=y, thick=OUTER, holes=north_holes)
        m.wall((X0, Z0), (X0, Z1), "z", "brick", None, height=H, y0=y, thick=OUTER, holes=west_holes)
        m.wall((X1, Z0), (X1, Z1), "z", "brick", None, height=H, y0=y, thick=OUTER, holes=east_holes, inner=None)

        # The corridor between z -1 and 1.5, the stairwell's wall at x 6, the flats either side.
        north_doors = [(3.0, 4.4, 0.0, DOOR_H), (14.0, 15.4, 0.0, DOOR_H)]
        m.wall((X0, -1.0), (6.0, -1.0), "x", "paint", plaster, height=H, y0=y, holes=north_doors)
        m.wall((6.0, Z0), (6.0, -1.0), "z", "paint", plaster, height=H, y0=y)
        m.wall((X0, 1.5), (X1, 1.5), "x", "paint", plaster, height=H, y0=y,
               holes=[(4.0, 5.4, 0.0, DOOR_H), (12.0, 13.4, 0.0, DOOR_H), (21.0, 22.4, 0.0, DOOR_H)])
        m.wall((-5.0, Z0), (-5.0, -1.0), "z", "paint", plaster, height=H, y0=y)
        m.wall((-7.0, 1.5), (-7.0, Z1), "z", "paint", plaster, height=H, y0=y)
        m.wall((1.5, 1.5), (1.5, Z1), "z", "paint", plaster, height=H, y0=y)
        # In each north flat, a wall between the living room and the bedroom, with a door.
        # (The east flat's bedroom is through its bathroom.)
        m.wall((X0, -6.5), (-5.0, -6.5), "x", "paint", plaster, height=H, y0=y, holes=[(7.0, 8.4, 0.0, DOOR_H)])
        m.wall((-5.0, -6.5), (6.0, -6.5), "x", "paint", plaster, height=H, y0=y, holes=[(7.5, 8.9, 0.0, DOOR_H)])

        # Lights and furniture, per floor.
        for (a, b, c, d) in [(X0, -6.5, -5.0, -1.0), (-5.0, -6.5, 6.0, -1.0), (X0, Z0, -5.0, -6.5), (-5.0, Z0, 6.0, -6.5),
                             (X0, 1.5, -7.0, Z1), (-7.0, 1.5, 1.5, Z1), (1.5, 1.5, X1, Z1), (X0, -1.0, 6.0, 1.5)]:
            cx, cz = (a + c) / 2, (b + d) / 2
            m.lamp_panel(cx, cz, y + H)
            m.light(cx, y + H - 0.5, cz, rng=9.0, energy=1.0)
        m.light(8.0, y + H - 0.5, -6.0, rng=9.0, energy=0.9)

        living_room(m, X0 + 0.15, -6.4, -5.1, -1.1, y=y)
        bedroom(m, X0 + 0.15, Z0 + 0.15, -5.1, -6.6, y=y, double=(k != 1))
        living_room(m, -4.9, -6.4, 5.9, -1.1, y=y)
        bedroom(m, -4.9, Z0 + 0.15, 1.4, -6.6, y=y, double=(k == 1))
        bath(m, 1.6, Z0 + 0.15, 5.9, -6.6, y=y)
        m.wall((1.5, Z0), (1.5, -6.5), "z", "paint", plaster, height=H, y0=y, holes=[(2.0, 3.4, 0.0, DOOR_H)])
        # The studios to the south: a kitchen along the back wall and a bed. On the ground floor
        # the middle one is the lobby, which the front door opens into.
        for (a, c) in [(X0 + 0.15, -7.1), (-6.9, 1.4), (1.6, X1 - 0.15)]:
            if k == 0 and a == -6.9:
                m.prop("furniture_bench_cushion", -6.4, 3.4, 90.0)
                m.prop("furniture_potted_plant", 0.9, 5.4)
                m.prop("furniture_potted_plant", -6.5, 2.0)
                m.span(-1.0, 1.35, 0.0, 2.0, 2.4, 2.85, "locker", (0.8, 0.7, 0.5))
                continue
            kitchen_row(m, a, c - 1.6, Z1 - 0.15, "s", y=y)
            m.against("furniture_bed_single", "w", a, 3.0, y=y)
            m.prop("furniture_table_round", c - 1.4, 3.3, y=y)
        # The corridor: a bench, a plant, the bins.
        m.prop("furniture_potted_plant", X0 + 0.5, 0.25, y=y)
        m.prop("furniture_bench", -9.6, 1.05, 180.0, y=y)
        m.prop("furniture_trashcan", -6.0, 1.1, y=y)
        m.prop("furniture_coat_rack_standing", -7.0, -0.6, y=y)

    # The stairwell: a switchback per storey, and one more up to the roof hut.
    for y in Y:
        flight(m, 6.0, 8.0, -6.5, -2.0, y, y + 1.7, "n", "concrete", (0.75, 0.75, 0.74))
        m.span(6.0, 10.0, y + 1.5, y + 1.7, -11.7, -6.5, "concrete", (0.75, 0.75, 0.74))
        flight(m, 8.0, 10.0, -6.5, -2.0, y + 1.7, y + 3.4, "s", "concrete", (0.75, 0.75, 0.74))
        railing(m, 8.0, -6.5, 8.0, -2.0, y + 1.7, height=0.9)
    # The hut on the roof over the stairwell, with its door to the roof.
    m.wall((6.0, Z0), (6.0, -1.0), "z", "brick", None, height=2.8, y0=TOP)
    m.wall((X1, Z0), (X1, -1.0), "z", "brick", None, height=2.8, y0=TOP)
    m.wall((6.0, Z0), (X1, Z0), "x", "brick", None, height=2.8, y0=TOP)
    m.wall((6.0, -1.0), (X1, -1.0), "x", "brick", None, height=2.8, y0=TOP, holes=[(0.5, 2.0, 0.0, DOOR_H)])
    m.span(5.8, 10.2, TOP + 2.8, TOP + 3.0, Z0 - 0.2, -0.8, "roof")
    m.light(8.0, TOP + 2.3, -6.0, rng=7.0, energy=0.8)

    # The roof: a parapet, a water tank on legs, air conditioners, a pigeon shed, deck chairs.
    railing(m, X0, Z1, X1, Z1, TOP, height=1.1, mat="brick", tint=None)
    m.span(X0 - 0.15, X0 + 0.15, TOP, TOP + 1.1, Z0, Z1, "brick")
    m.span(X1 - 0.15, X1 + 0.15, TOP, TOP + 1.1, -1.0, Z1, "brick")
    m.span(X0, 6.0, TOP, TOP + 1.1, Z0 - 0.15, Z0 + 0.15, "brick")
    for lx, lz in ((-12.5, -9.5), (-9.5, -9.5), (-12.5, -6.5), (-9.5, -6.5)):
        m.span(lx - 0.1, lx + 0.1, TOP, TOP + 1.6, lz - 0.1, lz + 0.1, "metal")
    m.span(-13.2, -8.8, TOP + 1.6, TOP + 3.6, -10.2, -5.8, "metal", (0.55, 0.6, 0.62))
    for i, ax in enumerate((-4.0, -2.4, -0.8)):
        m.span(ax - 0.6, ax + 0.6, TOP, TOP + 1.1, -10.0, -8.9, "metal", (0.85, 0.86, 0.86))
    m.wall((-6.0, 1.5), (-1.0, 1.5), "x", "wood", (0.6, 0.45, 0.3), height=2.2, y0=TOP, thick=0.12)
    m.wall((-6.0, 4.5), (-1.0, 4.5), "x", "wood", (0.6, 0.45, 0.3), height=2.2, y0=TOP, thick=0.12, holes=[(1.8, 3.0, 0.0, 2.0)])
    m.wall((-6.0, 1.5), (-6.0, 4.5), "z", "wood", (0.6, 0.45, 0.3), height=2.2, y0=TOP, thick=0.12)
    m.wall((-1.0, 1.5), (-1.0, 4.5), "z", "wood", (0.6, 0.45, 0.3), height=2.2, y0=TOP, thick=0.12)
    m.span(-6.2, -0.8, TOP + 2.2, TOP + 2.35, 1.3, 4.7, "roof")
    m.prop("furniture_cardboard_box_closed", -5.3, 2.2, 10.0, y=TOP)
    m.prop("furniture_cardboard_box_open", -4.2, 2.3, -5.0, y=TOP)
    m.prop("furniture_lounge_chair_relax", 3.0, 3.5, 200.0, y=TOP)
    m.prop("furniture_lounge_chair_relax", 1.0, 3.6, 160.0, y=TOP)
    m.prop("nature_pot_large", -14.4, 4.4, y=TOP)
    m.prop("furniture_potted_plant", 4.6, -9.6, y=TOP)
    m.prop("furniture_television_antenna", 0.0, -5.0, y=TOP)

    # The fire escape on the west face: a flight from the alley to the first floor's platform,
    # and a second from there to the second floor's, beside it.
    fx0, fx1 = -18.6, -16.15
    m.span(fx0, fx1, Y[1] - 0.15, Y[1], -6.0, -2.0, "metal", (0.3, 0.3, 0.32))
    flight(m, fx0, -17.4, -2.0, 3.6, 0.0, Y[1], "n", "metal", (0.3, 0.3, 0.32), thick=0.12)
    flight(m, -17.4, fx1, -2.0, 3.6, Y[1], Y[2], "s", "metal", (0.3, 0.3, 0.32), thick=0.12)
    m.span(fx0, fx1, Y[2] - 0.15, Y[2], 3.6, 6.0, "metal", (0.3, 0.3, 0.32))
    railing(m, fx0, -6.0, fx0, -2.0, Y[1])
    railing(m, fx0, -6.0, fx1, -6.0, Y[1])
    railing(m, fx0, 3.6, fx0, 6.0, Y[2])
    railing(m, fx0, 6.0, fx1, 6.0, Y[2])

    # The alley to the west: dumpsters, bins, crates, a parked van.
    for dz in (-20.0, -15.5):
        m.span(-29.0, -27.2, 0.0, 1.3, dz, dz + 1.2, "metal", (0.2, 0.42, 0.25))
    m.prop("furniture_trashcan", -29.2, -11.0)
    m.prop("furniture_trashcan", -28.6, -10.6, 30.0)
    m.prop("furniture_cardboard_box_closed", -24.0, -22.0, 12.0)
    m.prop("furniture_cardboard_box_open", -23.2, -21.6, -20.0)
    m.prop("furniture_cardboard_box_closed", -23.8, -22.1, 40.0).update({"at": v3(-24.0, 0.65, -22.0)})
    car(m, -25.0, -2.0, "z", (0.85, 0.85, 0.88))
    m.span(-26.2, -23.8, 1.0, 2.4, -4.2, -0.4, "metal", (0.85, 0.85, 0.88))

    # The back yard to the north: grass, trees, benches, a bike shelter, a low wall to vault.
    m.overlay(-15.0, -26.5, 9.0, -14.0, "grass")
    for (tx, tz, kind) in ((-12.0, -22.0, "nature_tree_oak"), (4.0, -23.0, "nature_tree_default"), (-3.0, -25.0, "nature_tree_small")):
        m.prop(kind, tx, tz, 25.0)
    m.prop("furniture_bench", -6.0, -18.0, 0.0)
    m.prop("furniture_bench", -2.0, -18.0, 0.0)
    m.prop("nature_plant_bush_detailed", -9.0, -15.4)
    m.prop("nature_plant_bush_detailed", 7.6, -15.6)
    m.prop("nature_plant_bush_large", 1.0, -15.2)
    m.span(12.0, 21.0, 0.0, 2.4, -25.5, -25.2, "wood", (0.55, 0.4, 0.28))
    m.span(12.0, 21.0, 2.4, 2.5, -25.6, -22.0, "roof")
    for px in (12.1, 16.5, 20.9):
        m.span(px - 0.08, px + 0.08, 0.0, 2.4, -22.1, -21.9, "metal")
    m.span(-15.0, 9.0, 0.0, 0.9, -13.5, -13.2, "brick")

    # The corner shop to the east: aisles of shelves, a counter, a back room.
    sx0, sx1, sz0, sz1 = 13.0, 22.0, -6.0, 6.0
    m.room(sx0, sz0, sx1, sz1, "tile_check", None, height=3.2, lights=1, energy=1.2)
    m.wall((sx0, sz0), (sx1, sz0), "x", "brick", None, thick=OUTER, inner="paint", inner_tint=(0.95, 0.95, 0.9))
    m.wall((sx0, sz1), (sx1, sz1), "x", "brick", None, thick=OUTER, holes=[(1.0, 3.0, SILL, HEAD), (4.0, 5.6, 0.0, DOOR_H), (6.6, 8.4, SILL, HEAD)])
    m.wall((sx0, sz0), (sx0, sz1), "z", "brick", None, thick=OUTER, holes=[(9.0, 10.4, 0.0, DOOR_H)])
    m.wall((sx1, sz0), (sx1, sz1), "z", "brick", None, thick=OUTER)
    m.wall((sx0, -2.5), (sx1, -2.5), "x", "paint", (0.95, 0.95, 0.9), holes=[(6.5, 7.9, 0.0, DOOR_H)])
    for az in (0.0, 2.6):
        for i in range(4):
            m.prop("furniture_kitchen_bar", 15.5 + i * 1.0, az, 0.0)
            m.prop("furniture_kitchen_bar", 15.5 + i * 1.0, az + 0.5, 180.0)
    m.prop("furniture_kitchen_fridge_large", sx1 - 0.65, 1.0, -90.0)
    m.prop("furniture_bear", 18.0, 4.8, 200.0)
    m.prop("furniture_kitchen_fridge_large", sx1 - 0.65, 2.3, -90.0)
    m.prop("furniture_desk", 14.6, 4.6, 90.0)
    m.prop("furniture_cardboard_box_closed", 14.0, -4.6, 5.0)
    m.prop("furniture_cardboard_box_closed", 14.7, -4.8, 35.0)
    m.prop("furniture_cardboard_box_open", 16.0, -4.5, -10.0)
    m.prop("furniture_bookcase_closed_wide", 19.5, -5.4)

    # The street: parked cars both sides.
    for i, x in enumerate((-26.0, -16.0, -4.0, 8.0, 18.0)):
        car(m, x, 12.2, "x", [(0.7, 0.12, 0.1), (0.15, 0.3, 0.6), (0.85, 0.85, 0.85), (0.2, 0.2, 0.22), (0.9, 0.75, 0.2)][i])
    for i, x in enumerate((-22.0, -8.0, 4.0, 14.0)):
        car(m, x, 17.8, "x", [(0.3, 0.5, 0.35), (0.6, 0.6, 0.62), (0.5, 0.15, 0.4), (0.15, 0.15, 0.5)][i])
    for x in (-24.0, -12.0, 0.0, 12.0):
        m.prop("nature_tree_small", x, 9.4)

    for (x, z) in ((-12.0, 0.2), (-8.0, 0.2), (-3.0, 0.2), (2.0, 0.2), (-12.0, -3.5), (-2.0, -3.5), (-10.0, 3.5), (-3.0, 3.5)):
        m.spawn("hiders", x, z)
    for (x, z) in ((-10.0, 15.0), (-6.0, 15.0), (-2.0, 15.0), (2.0, 15.0)):
        m.spawn("seekers", x, z)
    return m.build()


def warehouse():
    """Dockside Warehouse: a hall of racks and crates at dusk, a mezzanine with an office over
    it, and a container yard on the quay outside.

    Low and dark rather than tall: the racks' bottom bays are crawl spaces, the crate stacks are
    a maze at head height, and the yard's containers are rooms with one way in. The quay's edge
    is a fall into the harbour, which on this map is a catch."""
    m = Map(
        "hs_warehouse", "Dockside Warehouse",
        "Racks, crates and containers on the quay at dusk. Crawl, climb, and mind the water.",
        {"sky_top": [0.24, 0.30, 0.50], "sky_horizon": [0.82, 0.66, 0.56], "ground": [0.25, 0.23, 0.22],
         "sun_pitch": -16, "sun_yaw": 250, "sun_energy": 0.8, "sun_colour": [1.0, 0.78, 0.6],
         "ambient": [0.5, 0.52, 0.6], "ambient_energy": 0.45, "fog": 0.0012},
        kill_y=-3.0,
    )
    HX0, HX1, HZ0, HZ1 = -22.0, 14.0, -14.0, 10.0
    HH = 8.0
    steel = (0.58, 0.62, 0.66)

    # The quay: concrete to z 30, then the water.
    m.floor(-36, -30, 30, 30, "concrete", (0.78, 0.78, 0.76))
    m.span(-36, 30, -6.2, -6.0, 30, 46, "sand", (0.4, 0.38, 0.32))
    m.span(-36, 30, -0.9, -0.8, 30, 46, "water", None, solid=False)
    boundary(m, -36, -30, 30, 46, height=3.0, mat="metal", tint=(0.45, 0.48, 0.5))
    for bx in (-30.0, -18.0, -6.0, 6.0, 18.0):
        m.span(bx - 0.25, bx + 0.25, 0.0, 0.7, 29.2, 29.7, "metal", (0.25, 0.25, 0.27))

    # The hall.
    m.floor(HX0, HZ0, HX1, HZ1, "concrete", (0.68, 0.68, 0.66), y=0.02, thick=0.02)
    m.ceiling(HX0, HZ0, HX1, HZ1, HH, "metal", steel, "roof", None, 0.3)
    m.wall((HX0, HZ0), (HX1, HZ0), "x", "metal", steel, height=HH, thick=OUTER,
           holes=[(4.0, 7.0, 5.6, 6.8), (12.0, 15.0, 5.6, 6.8), (24.0, 27.0, 5.6, 6.8)])
    m.wall((HX0, HZ1), (HX1, HZ1), "x", "metal", steel, height=HH, thick=OUTER,
           holes=[(6.0, 10.0, 0.0, 4.5), (16.0, 20.0, 0.0, 4.5), (30.0, 31.4, 0.0, DOOR_H)])
    m.wall((HX0, HZ0), (HX0, HZ1), "z", "metal", steel, height=HH, thick=OUTER, holes=[(10.0, 13.0, 5.6, 6.8)])
    m.wall((HX1, HZ0), (HX1, HZ1), "z", "metal", steel, height=HH, thick=OUTER, holes=[(6.0, 7.4, 0.0, DOOR_H), (14.0, 17.0, 5.6, 6.8)])
    for (lx, lz) in ((-16.0, -8.0), (-6.0, -8.0), (4.0, 2.0), (-16.0, 4.0), (-6.0, 4.0), (8.0, -4.0)):
        m.lamp_panel(lx, lz, HH, w=1.6, d=0.5)
        m.light(lx, HH - 1.0, lz, rng=14.0, energy=0.8, colour=(1.0, 0.88, 0.7))

    # The racks: four rows, crawl spaces in every bottom bay.
    for i, z in enumerate((-11.0, -7.0, -3.0, 1.0)):
        rack(m, -20.5, -6.0, z, seed=17 + i * 31)

    # The crates: a maze of stacks, one to three high.
    crate = (0.62, 0.48, 0.3)
    stacks = [(-4.0, 4.0, 2), (-2.6, 4.0, 1), (-4.0, 5.4, 3), (-1.0, 6.6, 2), (0.6, 4.2, 1), (0.6, 5.6, 2),
              (2.2, 7.4, 3), (3.6, 4.2, 2), (3.6, 7.4, 1), (5.2, 5.8, 2), (6.8, 4.2, 3), (6.8, 7.6, 1),
              (8.4, 6.0, 2), (-5.6, 7.6, 1), (-2.4, 8.4, 2)]
    for (cx, cz, n) in stacks:
        for j in range(n):
            w = 1.25 - 0.08 * j
            m.span(cx - w / 2, cx + w / 2, j * 1.2, (j + 1) * 1.2, cz - w / 2, cz + w / 2, "wood", crate)
    # Pallets of boxes by the doors.
    for (px, pz) in ((-14.0, 7.0), (-11.0, 7.4), (-4.0, 1.0)):
        m.span(px - 0.6, px + 0.6, 0.0, 0.15, pz - 0.6, pz + 0.6, "wood", (0.72, 0.6, 0.45))
        m.prop("furniture_cardboard_box_closed", px - 0.25, pz - 0.2, 10.0, y=0.15)
        m.prop("furniture_cardboard_box_closed", px + 0.3, pz + 0.2, -15.0, y=0.15)
        m.prop("furniture_cardboard_box_open", px, pz, 5.0, y=0.8)

    # A forklift.
    yellow = (0.92, 0.72, 0.12)
    m.span(-9.0, -7.8, 0.15, 1.6, 5.0, 7.2, "metal", yellow)
    m.span(-9.1, -7.7, 0.0, 2.6, 4.7, 4.85, "metal", (0.2, 0.2, 0.2))
    m.span(-8.9, -7.9, 0.1, 0.2, 3.6, 4.7, "metal", (0.2, 0.2, 0.2))

    # The mezzanine along the north wall, with the office on it and the stairs up.
    MY = 3.6
    m.span(-2.0, HX1 - 0.15, MY - 0.2, MY, HZ0 + 0.15, -8.0, "metal", (0.45, 0.47, 0.5))
    for px in (-1.8, 3.0, 8.0):
        m.span(px - 0.15, px + 0.15, 0.0, MY - 0.2, -8.3, -8.0, "metal", (0.3, 0.3, 0.32))
    railing(m, -2.0, -8.0, 11.9, -8.0, MY)
    railing(m, -2.0, HZ0 + 0.15, -2.0, -8.0, MY)
    flight(m, 12.0, 13.6, -8.0, -2.0, 0.0, MY, "n", "metal", (0.3, 0.3, 0.32), thick=0.15)
    m.wall((4.0, HZ0 + 0.15), (4.0, -9.0), "z", "paint", (0.85, 0.85, 0.8), height=2.6, y0=MY, thick=0.12)
    m.wall((4.0, -9.0), (HX1 - 0.15, -9.0), "x", "paint", (0.85, 0.85, 0.8), height=2.6, y0=MY, thick=0.12,
           holes=[(1.0, 3.0, 1.0, 2.0), (4.2, 5.6, 0.0, DOOR_H), (6.5, 8.5, 1.0, 2.0)])
    m.span(4.0, HX1 - 0.15, MY + 2.6, MY + 2.7, HZ0 + 0.15, -9.0, "ceiling")
    m.light(9.0, MY + 2.2, -11.5, rng=7.0, energy=1.0)
    m.against("furniture_desk", "n", HZ0 + 0.3, 6.5, y=MY)
    m.prop("furniture_chair_desk", 6.5, -11.4, 180.0, y=MY)
    m.against("furniture_bookcase_closed_doors", "e", HX1 - 0.15, -12.5, y=MY)
    m.against("furniture_bookcase_closed", "e", HX1 - 0.15, -11.4, y=MY)
    m.prop("furniture_lounge_sofa", 11.6, -9.9, 180.0, y=MY)
    m.prop("furniture_kitchen_fridge_small", 4.6, -13.3, 90.0, y=MY)
    for (bx, bz) in ((-1.0, -12.8), (0.0, -12.9), (-0.5, -11.8), (2.0, -13.0), (1.2, -10.0)):
        m.prop("furniture_cardboard_box_closed", bx, bz, (bx * 37) % 40, y=MY)
    # Under the mezzanine: the parts store.
    m.prop("furniture_bookcase_open", 0.5, -13.4, 0.0)
    m.prop("furniture_bookcase_open", 1.5, -13.4, 0.0)
    m.prop("furniture_bookcase_closed_wide", 5.0, -13.4, 0.0)
    m.prop("furniture_washer_dryer_stacked", 9.0, -13.3, 0.0)
    m.prop("furniture_cardboard_box_open", 6.0, -10.0, 20.0)

    # The yard: containers, some stacked, some open, one with stairs to its roof.
    red, blue, green, grey = (0.62, 0.22, 0.18), (0.18, 0.32, 0.6), (0.2, 0.45, 0.3), (0.5, 0.5, 0.52)
    container(m, -30.0, 14.0, "x", open_end="e", tint=blue)
    container(m, -30.0, 14.0, "x", y=2.6, tint=red)
    container(m, -30.0, 17.0, "x", open_end="e", tint=green)
    container(m, -30.0, 22.0, "x", tint=grey)
    container(m, -20.0, 20.0, "x", open_end="w", tint=red)
    flight(m, -14.0, -10.0, 20.1, 21.3, 0.0, 2.6, "w", "metal", (0.3, 0.3, 0.32), thick=0.12)
    container(m, -6.0, 18.0, "z", open_end="s", tint=green)
    container(m, -2.6, 18.0, "z", open_end="n", tint=blue)
    container(m, 6.0, 16.0, "x", open_end="w", tint=grey)
    container(m, 6.0, 16.0, "x", y=2.6, tint=green)
    container(m, 6.0, 22.0, "x", open_end="e", tint=red)
    container(m, 16.0, 13.0, "z", open_end="s", tint=blue)
    container(m, 19.0, 13.0, "z", tint=red)
    car(m, 22.0, 24.0, "x", (0.85, 0.85, 0.85))
    # The gatehouse.
    m.room(20.0, -24.0, 26.0, -18.0, "tile", None, height=2.8, lights=1)
    m.wall((20.0, -24.0), (26.0, -24.0), "x", "brick", None, height=2.8, thick=OUTER)
    m.wall((20.0, -18.0), (26.0, -18.0), "x", "brick", None, height=2.8, thick=OUTER, holes=[(0.6, 2.0, 0.0, DOOR_H), (3.0, 5.0, SILL, HEAD)])
    m.wall((20.0, -24.0), (20.0, -18.0), "z", "brick", None, height=2.8, thick=OUTER, holes=[(2.0, 4.0, SILL, HEAD)])
    m.wall((26.0, -24.0), (26.0, -18.0), "z", "brick", None, height=2.8, thick=OUTER)
    m.against("furniture_desk", "n", -23.85, 23.0)
    m.prop("furniture_chair_desk", 23.0, -22.3, 180.0)
    m.against("furniture_kitchen_fridge_small", "e", 25.85, -20.0)
    # Bins and boxes round the back.
    for (bx, bz) in ((-30.0, -24.0), (-28.8, -24.4), (-25.0, -26.0), (16.0, -26.0), (17.2, -26.4)):
        m.prop("furniture_trashcan", bx, bz, bx * 13 % 60)
    m.span(-14.0, -12.0, 0.0, 1.3, -24.0, -22.8, "metal", (0.2, 0.42, 0.25))

    for (x, z) in ((-14.0, -5.0), (-10.0, -5.0), (-14.0, -1.0), (-10.0, -1.0), (-2.0, 2.0), (2.0, 2.0), (8.0, 1.0), (-17.0, 8.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((10.0, 26.0), (14.0, 26.0), (2.0, 26.0), (-8.0, 26.0)):
        m.spawn("seekers", x, z)
    return m.build()


def manor():
    """Hollowmere Manor: a two-storey house with a hall open to the roof, a gallery round it,
    eight rooms, a hedge maze to the west and a garden with a greenhouse to the east.

    The hall is the hub: its stair and its gallery see everything, so a hider who crosses it is
    seen and one who waits in a wing is cornered. The maze is the opposite: nobody sees
    anybody, and dead ends are where it ends."""
    m = Map(
        "hs_manor", "Hollowmere Manor",
        "A house with a gallery over the hall, a hedge maze and a greenhouse. Mind the dead ends.",
        {"sky_top": [0.36, 0.52, 0.78], "sky_horizon": [0.86, 0.80, 0.70], "ground": [0.3, 0.32, 0.26],
         "sun_pitch": -32, "sun_yaw": 220, "sun_energy": 0.95, "sun_colour": [1.0, 0.9, 0.76],
         "ambient": [0.58, 0.6, 0.66], "ambient_energy": 0.42, "fog": 0.0015},
    )
    X0, X1, Z0, Z1 = -14.0, 14.0, -10.0, 8.0
    UP = 3.8
    H = 3.6
    wallpaper = (0.86, 0.80, 0.70)
    panel = (0.62, 0.46, 0.34)

    m.floor(-46, -20, 42, 28, "grass")
    boundary(m, -46, -20, 42, 28, height=2.6, mat="hedge", tint=(0.8, 1.0, 0.8))
    m.overlay(-1.5, 8.0, 1.5, 26.0, "sand", (0.92, 0.88, 0.78))
    m.overlay(-8.0, 11.0, 8.0, 17.0, "sand", (0.92, 0.88, 0.78))
    # The fountain on the drive.
    m.span(-2.2, 2.2, 0.0, 0.6, 11.8, 12.2, "stone")
    m.span(-2.2, 2.2, 0.0, 0.6, 15.8, 16.2, "stone")
    m.span(-2.2, -1.8, 0.0, 0.6, 12.2, 15.8, "stone")
    m.span(1.8, 2.2, 0.0, 0.6, 12.2, 15.8, "stone")
    m.span(-1.8, 1.8, 0.4, 0.45, 12.2, 15.8, "water", None, solid=False)
    m.span(-0.3, 0.3, 0.0, 2.2, 13.7, 14.3, "stone")

    # Floors: wood below, carpet above with the hall left open, and the landing.
    m.floor(X0, Z0, X1, Z1, "parquet")
    m.span(X0, -4.0, UP - 0.2, UP, Z0, Z1, "carpet", (0.6, 0.25, 0.25))
    m.span(4.0, X1, UP - 0.2, UP, Z0, Z1, "carpet", (0.6, 0.25, 0.25))
    m.span(-4.0, 4.0, UP - 0.2, UP, Z0, -6.0, "carpet", (0.6, 0.25, 0.25))
    m.span(-4.0, -2.6, UP - 0.2, UP, -6.0, Z1, "carpet", (0.6, 0.25, 0.25))
    m.span(2.6, 4.0, UP - 0.2, UP, -6.0, Z1, "carpet", (0.6, 0.25, 0.25))
    m.ceiling(X0, Z0, X1, Z1, UP + H, "ceiling", None, "roof", (0.45, 0.3, 0.28), 0.5)

    # The grand stair up the middle of the hall to the landing, and the gallery's rails.
    flight(m, -1.5, 1.5, -6.0, 2.0, 0.0, UP, "n", "wood", panel)
    railing(m, -2.6, -6.0, -2.6, Z1, UP, mat="wood", tint=panel)
    railing(m, 2.6, -6.0, 2.6, Z1, UP, mat="wood", tint=panel)
    railing(m, -2.6, -6.0, -1.5, -6.0, UP, mat="wood", tint=panel)
    railing(m, 1.5, -6.0, 2.6, -6.0, UP, mat="wood", tint=panel)
    m.light(0.0, UP + H - 0.8, 1.0, rng=12.0, energy=1.3, colour=(1.0, 0.9, 0.75))
    m.light(0.0, 2.6, 5.0, rng=8.0, energy=0.8, colour=(1.0, 0.9, 0.75))

    for k, y in enumerate((0.0, UP)):
        # Outer walls: stone outside, wallpaper in.
        if k == 0:
            south = [(2.0, 3.8, SILL, HEAD), (6.5, 8.3, SILL, HEAD), (13.3, 14.7, 0.0, DOOR_H), (19.7, 21.5, SILL, HEAD), (24.2, 26.0, SILL, HEAD)]
            north = [(3.0, 4.8, SILL, HEAD), (13.3, 14.7, 0.0, DOOR_H), (22.0, 23.8, SILL, HEAD)]
        else:
            south = [(2.0, 3.8, SILL, HEAD), (6.5, 8.3, SILL, HEAD), (12.6, 15.4, 1.0, 3.0), (19.7, 21.5, SILL, HEAD), (24.2, 26.0, SILL, HEAD)]
            north = [(3.0, 4.8, SILL, HEAD), (12.6, 15.4, 1.0, 3.0), (22.0, 23.8, SILL, HEAD)]
        m.wall((X0, Z1), (X1, Z1), "x", "stone", None, height=H, y0=y, thick=OUTER, holes=south, inner="paint", inner_tint=wallpaper)
        m.wall((X0, Z0), (X1, Z0), "x", "stone", None, height=H, y0=y, thick=OUTER, holes=north)
        m.wall((X0, Z0), (X0, Z1), "z", "stone", None, height=H, y0=y, thick=OUTER, holes=[(3.0, 4.8, SILL, HEAD), (12.0, 13.4, 0.0, DOOR_H) if k == 0 else (12.0, 13.8, SILL, HEAD)])
        m.wall((X1, Z0), (X1, Z1), "z", "stone", None, height=H, y0=y, thick=OUTER, holes=[(3.0, 4.8, SILL, HEAD), (12.0, 13.4, 0.0, DOOR_H) if k == 0 else (12.0, 13.8, SILL, HEAD)])
        # The wings' inner walls on the hall, with doors, and the walls between rooms.
        m.wall((-4.0, Z0), (-4.0, Z1), "z", "paint", wallpaper, height=H, y0=y, holes=[(3.0, 4.4, 0.0, DOOR_H), (13.0, 14.4, 0.0, DOOR_H)])
        m.wall((4.0, Z0), (4.0, Z1), "z", "paint", wallpaper, height=H, y0=y, holes=[(3.0, 4.4, 0.0, DOOR_H), (13.0, 14.4, 0.0, DOOR_H)])
        # Through-doors between the two ground-floor rooms of each wing; upstairs every room
        # opens on the gallery alone.
        m.wall((X0, -1.0), (-4.0, -1.0), "x", "paint", wallpaper, height=H, y0=y, holes=[(4.0, 5.4, 0.0, DOOR_H)] if k == 0 else [])
        m.wall((4.0, -1.0), (X1, -1.0), "x", "paint", wallpaper, height=H, y0=y, holes=[(4.6, 6.0, 0.0, DOOR_H)] if k == 0 else [])
        for (a, b, c, d) in ((X0, Z0, -4.0, -1.0), (X0, -1.0, -4.0, Z1), (4.0, Z0, X1, -1.0), (4.0, -1.0, X1, Z1)):
            cx, cz = (a + c) / 2, (b + d) / 2
            m.lamp_panel(cx, cz, y + H)
            m.light(cx, y + H - 0.5, cz, rng=10.0, energy=1.0, colour=(1.0, 0.92, 0.8))

    # Ground floor: dining room, library, kitchen, lounge; the hall's furniture.
    table = m.prop("furniture_table_cloth", -9.0, -5.5, 90.0)
    m.prop("furniture_table_cloth", -9.0, -3.5, 90.0)
    for dz in (-6.3, -5.0, -3.7, -2.4):
        m.prop("furniture_chair_cushion", -10.0, dz, 90.0)
        m.prop("furniture_chair_cushion", -8.0, dz, -90.0)
    m.on("furniture_lamp_round_table", table)
    m.against("furniture_cabinet_television_doors", "n", Z0 + 0.15, -9.0)
    m.against("furniture_potted_plant", "w", X0 + 0.15, -9.3)
    for i in range(4):
        m.against("furniture_bookcase_closed" if i % 2 else "furniture_bookcase_open", "w", X0 + 0.15, 4.0 + i * 0.95)
        m.against("furniture_bookcase_open", "s", Z1 - 0.15, -12.8 + i * 0.95)
    m.prop("furniture_lounge_chair_relax", -7.0, 2.6, 135.0)
    m.prop("furniture_lounge_chair", -9.0, 4.0, 90.0)
    m.prop("furniture_rug_round", -8.0, 3.2)
    m.prop("furniture_desk_corner", -5.3, 6.6, 180.0)
    kitchen_row(m, 4.2, X1 - 0.2, Z0 + 0.15, "n")
    m.prop("furniture_kitchen_bar", 8.0, -5.0, 0.0)
    m.prop("furniture_kitchen_bar", 9.0, -5.0, 0.0)
    m.prop("furniture_kitchen_bar_end", 9.6, -5.0, 0.0)
    m.prop("furniture_kitchen_fridge_large", X1 - 0.8, -2.0, -90.0)
    living_room(m, 4.2, -0.8, X1 - 0.2, Z1 - 0.2)
    m.prop("furniture_coat_rack_standing", -3.2, 6.8)
    m.prop("furniture_bench_cushion", 3.3, 6.5, -90.0)
    m.prop("furniture_potted_plant", -3.4, -5.5)
    m.prop("furniture_potted_plant", 3.4, -5.5)
    m.prop("furniture_rug_rectangle", 0.0, 5.2, 90.0)

    # Upstairs: three bedrooms, a bathroom and a study; a reading nook on the landing.
    bedroom(m, X0 + 0.15, Z0 + 0.15, -4.1, -1.1, y=UP)
    bedroom(m, X0 + 0.15, -0.9, -4.1, Z1 - 0.15, y=UP, double=False)
    bedroom(m, 4.1, Z0 + 0.15, X1 - 0.15, -1.1, y=UP)
    m.prop("furniture_bathtub", 6.0, 6.9, 0.0, y=UP)
    m.against("furniture_toilet", "e", X1 - 0.15, 2.0, y=UP)
    m.against("furniture_bathroom_sink_square", "e", X1 - 0.15, 3.3, y=UP)
    m.against("furniture_bathroom_cabinet_drawer", "n", -0.85, 12.5, y=UP)
    m.prop("furniture_lounge_chair", -2.6, -8.5, 45.0, y=UP)
    m.prop("furniture_bookcase_open_low", 2.2, -9.4, y=UP)
    m.prop("furniture_lamp_round_floor", 3.4, -9.4, y=UP)

    # The hedge maze, west of the house.
    hedge_maze(m, -42.0, -16.0, 9, 13, 2.6, seed=1408, gate_w=6, gate_e=8)
    m.prop("nature_pot_small", -30.3, 0.9)
    m.prop("furniture_bench", -30.3, 3.5, 0.0)

    # The garden: a greenhouse, a gazebo, a shed, beds of flowers, trees.
    gx0, gx1, gz0, gz1 = 20.0, 32.0, -12.0, -3.0
    m.floor(gx0, gz0, gx1, gz1, "tile_small", (0.75, 0.72, 0.68), y=0.05, thick=0.05)
    for (a, b, along) in (((gx0, gz0), (gx1, gz0), "x"), ((gx0, gz0), (gx0, gz1), "z"), ((gx1, gz0), (gx1, gz1), "z")):
        m.wall(a, b, along, "glass", None, height=3.0, thick=0.06)
    m.wall((gx0, gz1), (gx1, gz1), "x", "glass", None, height=3.0, thick=0.06, holes=[(5.3, 6.7, 0.0, DOOR_H)])
    m.span(gx0, gx1, 3.0, 3.06, gz0, gz1, "glass")
    for i, tx in enumerate((22.5, 26.0, 29.5)):
        for tz in (-10.0, -6.5):
            t = m.prop("furniture_table", tx, tz, 90.0, y=0.05)
            m.on("furniture_plant_small%d" % (i % 3 + 1), t, dz=-0.3)
            m.on("furniture_plant_small%d" % ((i + 1) % 3 + 1), t, dz=0.4)
    m.prop("furniture_potted_plant", 20.6, -3.6, y=0.05)
    m.prop("nature_pot_large", 31.2, -11.2, y=0.05)
    # The gazebo.
    m.span(23.0, 29.0, 0.0, 0.3, 4.0, 10.0, "wood", (0.8, 0.75, 0.68))
    for px in (23.2, 28.8):
        for pz in (4.2, 9.8):
            m.span(px - 0.12, px + 0.12, 0.3, 3.0, pz - 0.12, pz + 0.12, "wood", (0.92, 0.9, 0.86))
    m.span(22.6, 29.4, 3.0, 3.3, 3.6, 10.4, "roof", (0.4, 0.3, 0.3))
    m.prop("furniture_bench_cushion", 24.0, 7.0, 90.0, y=0.3)
    m.prop("furniture_bench_cushion", 28.0, 7.0, -90.0, y=0.3)
    m.prop("furniture_table_round", 26.0, 7.0, y=0.3)
    # The shed.
    m.room(34.0, 12.0, 39.0, 18.0, "wood", (0.6, 0.5, 0.4), height=2.6, ceiling="wood", roof="roof", lights=0)
    m.wall((34.0, 12.0), (39.0, 12.0), "x", "wood", (0.5, 0.38, 0.28), height=2.6, thick=0.12)
    m.wall((34.0, 18.0), (39.0, 18.0), "x", "wood", (0.5, 0.38, 0.28), height=2.6, thick=0.12)
    m.wall((34.0, 12.0), (34.0, 18.0), "z", "wood", (0.5, 0.38, 0.28), height=2.6, thick=0.12, holes=[(2.0, 3.4, 0.0, DOOR_H)])
    m.wall((39.0, 12.0), (39.0, 18.0), "z", "wood", (0.5, 0.38, 0.28), height=2.6, thick=0.12)
    m.prop("furniture_cardboard_box_closed", 37.8, 13.0, 15.0)
    m.prop("furniture_cardboard_box_open", 38.0, 14.0, -5.0)
    m.prop("furniture_bench", 38.3, 16.5, -90.0)
    m.prop("nature_log_stack", 36.0, 17.0, 90.0)
    for (fx, fz, kind) in ((18.0, 2.0, "nature_flower_red_a"), (18.6, 2.4, "nature_flower_yellow_a"), (19.2, 2.0, "nature_flower_purple_a"),
                           (18.0, 14.0, "nature_plant_bush_detailed"), (21.0, 16.0, "nature_plant_bush_large"), (30.0, 20.0, "nature_plant_bush_detailed"),
                           (12.0, 22.0, "nature_plant_bush_detailed"), (-12.0, 22.0, "nature_plant_bush_detailed"), (-10.0, 13.0, "nature_plant_bush_large"),
                           (10.0, 13.0, "nature_plant_bush_large"), (16.0, -14.0, "nature_crop_pumpkin"), (17.4, -14.2, "nature_crop_pumpkin")):
        m.prop(kind, fx, fz)
    for (tx, tz, kind) in ((-14.0, 20.0, "nature_tree_oak"), (14.0, 20.0, "nature_tree_oak"), (36.0, -15.0, "nature_tree_default"),
                           (38.0, 2.0, "nature_tree_fat"), (-20.0, 22.0, "nature_tree_tall"), (20.0, 24.0, "nature_tree_detailed"),
                           (-16.0, -16.0, "nature_tree_pine_round_a"), (8.0, -16.5, "nature_tree_default_fall")):
        m.prop(kind, tx, tz, (tx * 7) % 90)

    for (x, z) in ((-1.0, 4.5), (1.0, 4.5), (-2.0, 6.5), (2.0, 6.5), (-6.0, 4.0), (6.0, -4.0), (-8.0, -8.0), (8.0, 4.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((-4.0, 22.0), (0.0, 22.0), (4.0, 22.0), (0.0, 25.0)):
        m.spawn("seekers", x, z)
    return m.build()


def village():
    """Thistle Green: a village round a square with a fountain and a market, six cottages, a
    barn with a hayloft, a meeting hall, a pond with a bridge and the gardens between.

    Wide rather than tall, and full of corners: every cottage has a front and a back door, so
    a hider can go in one and out the other, and the gardens' fences are low enough to vault
    and high enough to crouch behind."""
    m = Map(
        "hs_village", "Thistle Green",
        "Cottages round a square, a barn with a hayloft, a pond and its bridge. Every house has a back door.",
        {"sky_top": [0.32, 0.55, 0.88], "sky_horizon": [0.78, 0.86, 0.94], "ground": [0.36, 0.38, 0.28],
         "sun_pitch": -42, "sun_yaw": 60, "sun_energy": 1.0, "sun_colour": [1.0, 0.96, 0.86],
         "ambient": [0.6, 0.64, 0.7], "ambient_energy": 0.45, "fog": 0.0012},
    )
    px0, px1, pz0, pz1 = -40.0, -28.0, 28.0, 40.0
    m.floor_around(-44, -44, 44, 44, (px0, pz0, px1, pz1), "grass")
    boundary(m, -44, -44, 44, 44, height=2.6, mat="hedge", tint=(0.8, 1.0, 0.8))

    # The square, the fountain, the market.
    m.overlay(-11, -11, 11, 11, "stone", (0.85, 0.82, 0.76))
    for (a, b, c, d) in ((-1.5, -36, 1.5, -11), (-1.5, 11, 1.5, 26), (-36, -1.5, -11, 1.5), (11, -1.5, 36, 1.5)):
        m.overlay(a, b, c, d, "dirt")
    m.span(-2.6, 2.6, 0.0, 0.7, -2.6, -2.2, "stone")
    m.span(-2.6, 2.6, 0.0, 0.7, 2.2, 2.6, "stone")
    m.span(-2.6, -2.2, 0.0, 0.7, -2.2, 2.2, "stone")
    m.span(2.2, 2.6, 0.0, 0.7, -2.2, 2.2, "stone")
    m.span(-2.2, 2.2, 0.5, 0.55, -2.2, 2.2, "water", None, solid=False)
    m.span(-0.4, 0.4, 0.0, 2.6, -0.4, 0.4, "stone")
    for (sx, sz, cloth) in ((-7.0, -8.0, (0.85, 0.3, 0.25)), (5.0, -8.0, (0.25, 0.5, 0.85)), (-7.0, 6.0, (0.9, 0.8, 0.3)), (5.0, 6.0, (0.35, 0.7, 0.4))):
        stall(m, sx, sz, cloth)
    m.prop("furniture_bench", -6.0, -3.0, 90.0)
    m.prop("furniture_bench", 6.0, 3.0, -90.0)
    m.prop("nature_tree_oak", 8.5, -8.5)

    def cottage(cx, cz, face, idx):
        """A cottage of two rooms, 9 by 7, its front door on `face` ('n' or 's'), a back door
        opposite, and a fenced garden behind."""
        x0, x1 = cx - 4.5, cx + 4.5
        z0, z1 = cz - 3.5, cz + 3.5
        tint = [(0.95, 0.92, 0.82), (0.9, 0.82, 0.72), (0.88, 0.9, 0.94), (0.95, 0.88, 0.86), (0.86, 0.92, 0.84), (0.94, 0.94, 0.9)][idx % 6]
        m.room(x0, z0, x1, z1, "wood_floor", None, height=2.8, roof_tint=(0.55, 0.28, 0.22), eave=0.5, lights=1, energy=0.9)
        front = [(1.0, 2.6, SILL, HEAD), (3.8, 5.2, 0.0, DOOR_H), (6.4, 8.0, SILL, HEAD)]
        back = [(1.0, 2.4, 0.0, DOOR_H), (6.4, 8.0, SILL, HEAD)]
        m.wall((x0, z0), (x1, z0), "x", "plaster", tint, height=2.8, thick=OUTER, holes=front if face == "n" else back)
        m.wall((x0, z1), (x1, z1), "x", "plaster", tint, height=2.8, thick=OUTER, holes=front if face == "s" else back)
        m.wall((x0, z0), (x0, z1), "z", "plaster", tint, height=2.8, thick=OUTER, holes=[(2.6, 4.2, SILL, HEAD)])
        m.wall((x1, z0), (x1, z1), "z", "plaster", tint, height=2.8, thick=OUTER, holes=[(2.6, 4.2, SILL, HEAD)])
        m.wall((cx + 0.5, z0), (cx + 0.5, z1), "z", "paint", (0.95, 0.93, 0.88), height=2.8, holes=[(2.8, 4.2, 0.0, DOOR_H)])
        # Living room and kitchen west of the partition, the bedroom east.
        m.prop("furniture_lounge_sofa", x0 + 1.5, cz, 90.0)
        m.prop("furniture_table_round", cx - 1.4, cz + (1.2 if face == "n" else -1.2))
        m.prop("furniture_chair", cx - 1.4, cz + (2.1 if face == "n" else -2.1), 180.0 if face == "n" else 0.0)
        m.against("furniture_kitchen_stove", "w", x0 + 0.15, cz + (2.5 if face == "n" else -2.5))
        m.against("furniture_bookcase_closed", "w", x0 + 0.15, cz + (-2.4 if face == "n" else 2.4))
        m.prop("furniture_bed_single", x1 - 1.2, cz, 0.0)
        m.against("furniture_bookcase_closed_doors", "e", x1 - 0.15, cz + (2.6 if face == "n" else -2.6))
        m.prop("furniture_cabinet_bed_drawer", cx + 1.2, cz + (-2.8 if face == "n" else 2.8))
        # The garden behind: a low fence with a gate, a vegetable patch or a woodpile.
        gz0, gz1 = (z1, z1 + 6.0) if face == "n" else (z0 - 6.0, z0)
        fence(m, x0, gz0, x1, gz1, height=1.1, gaps=[("w", 2.0, 3.4)] if idx % 2 else [("e", 2.0, 3.4)])
        gm = (gz0 + gz1) / 2
        if idx % 2:
            for i in range(3):
                m.prop("nature_crop_pumpkin" if i % 2 else "nature_crop_melon", cx - 2.5 + i * 2.0, gm)
        else:
            m.prop("nature_log_stack", cx - 2.0, gm, 90.0)
            m.prop("nature_plant_bush_detailed", cx + 2.4, gm)

    for i, (cx, cz, face) in enumerate(((-22.0, -20.0, "s"), (6.0, -24.0, "s"), (22.0, -20.0, "s"),
                                       (-22.0, 20.0, "n"), (24.0, 20.0, "n"), (-28.0, -6.0, "s"))):
        cottage(cx, cz, face, i)

    # The barn, east: a hayloft over the back half, stairs up to it, stalls and bales below.
    bx0, bx1, bz0, bz1 = 20.0, 34.0, -7.0, 7.0
    BH = 6.0
    m.floor(bx0, bz0, bx1, bz1, "dirt", (0.8, 0.7, 0.55), y=0.02, thick=0.02)
    m.ceiling(bx0, bz0, bx1, bz1, BH, "wood", (0.6, 0.45, 0.3), "roof", (0.5, 0.2, 0.18), 0.4)
    red = (0.62, 0.22, 0.18)
    m.wall((bx0, bz0), (bx1, bz0), "x", "wood", red, height=BH, thick=OUTER, holes=[(5.0, 6.4, 0.0, DOOR_H)])
    m.wall((bx0, bz1), (bx1, bz1), "x", "wood", red, height=BH, thick=OUTER, holes=[(9.0, 10.4, 0.0, DOOR_H)])
    m.wall((bx0, bz0), (bx0, bz1), "z", "wood", red, height=BH, thick=OUTER, holes=[(5.0, 9.0, 0.0, 4.0)])
    m.wall((bx1, bz0), (bx1, bz1), "z", "wood", red, height=BH, thick=OUTER, holes=[(5.5, 8.5, 3.6, 5.0)])
    LY = 3.0
    m.span(27.0, bx1 - 0.15, LY - 0.2, LY, bz0 + 0.15, bz1 - 0.15, "wood", (0.7, 0.55, 0.38))
    for pz in (-5.0, 0.0, 5.0):
        m.span(26.85, 27.15, 0.0, LY - 0.2, pz - 0.15, pz + 0.15, "wood", (0.5, 0.36, 0.25))
    railing(m, 27.0, bz0 + 0.15, 27.0, 3.4, LY, mat="wood", tint=(0.5, 0.36, 0.25))
    flight(m, 21.0, 27.0, 5.4, bz1 - 0.2, 0.0, LY, "e", "wood", (0.6, 0.45, 0.3))
    hay = (0.95, 0.82, 0.45)
    bales = [(29.0, -5.5, 0), (30.3, -5.5, 0), (29.6, -5.5, 1), (32.5, -4.0, 0), (32.5, -2.6, 0), (32.5, -3.3, 1),
             (30.0, 1.0, 0), (31.3, 1.0, 0), (32.6, 1.0, 0), (31.3, 1.0, 1), (29.5, 4.0, 0)]
    for (hx, hz, level) in bales:
        m.span(hx - 0.6, hx + 0.6, LY + level * 0.8, LY + (level + 1) * 0.8, hz - 0.4, hz + 0.4, "sand", hay)
    for (hx, hz, level) in ((22.0, -5.6, 0), (22.0, -4.8, 0), (22.0, -5.2, 1), (24.0, 3.2, 0)):
        m.span(hx - 0.6, hx + 0.6, level * 0.8, (level + 1) * 0.8, hz - 0.4, hz + 0.4, "sand", hay)
    for sz in (-6.85, -3.0, 1.0):
        m.span(27.5, bx1 - 0.15, 0.0, 1.4, sz, sz + 0.1, "wood", (0.55, 0.4, 0.28))
    m.prop("nature_log_stack", 31.0, 4.8, 0.0)
    m.prop("furniture_cardboard_box_open", 25.0, -1.0, 20.0)
    m.light(24.0, 5.0, 0.0, rng=10.0, energy=0.8, colour=(1.0, 0.85, 0.6))
    m.light(31.0, 5.2, 0.0, rng=8.0, energy=0.7, colour=(1.0, 0.85, 0.6))

    # The meeting hall, north: rows of benches and a stage.
    hx0, hx1, hz0, hz1 = -8.0, 8.0, 26.0, 36.0
    m.room(hx0, hz0, hx1, hz1, "parquet", None, height=4.0, roof_tint=(0.35, 0.35, 0.4), lights=2, energy=1.0)
    m.wall((hx0, hz0), (hx1, hz0), "x", "stone", None, height=4.0, thick=OUTER, holes=[(2.0, 3.8, 1.0, 3.0), (7.3, 8.7, 0.0, DOOR_H), (12.2, 14.0, 1.0, 3.0)])
    m.wall((hx0, hz1), (hx1, hz1), "x", "stone", None, height=4.0, thick=OUTER, holes=[(1.0, 2.4, 0.0, DOOR_H)])
    m.wall((hx0, hz0), (hx0, hz1), "z", "stone", None, height=4.0, thick=OUTER, holes=[(3.0, 5.0, 1.0, 3.0)])
    m.wall((hx1, hz0), (hx1, hz1), "z", "stone", None, height=4.0, thick=OUTER, holes=[(3.0, 5.0, 1.0, 3.0)])
    m.span(-6.0, 6.0, 0.0, 0.6, 32.6, hz1 - 0.15, "wood", (0.55, 0.4, 0.3))
    for row in range(4):
        for bx in (-4.5, -2.5, 2.5, 4.5):
            m.prop("furniture_bench", bx, 28.0 + row * 1.1, 180.0)
    m.prop("furniture_speaker", -5.4, 34.5, y=0.6)
    m.prop("furniture_speaker", 5.4, 34.5, y=0.6)
    m.prop("furniture_desk", 0.0, 34.0, 180.0, y=0.6)

    # The pond, north-west (its hole is left out of the field), with a bridge over it high
    # enough to crouch under in the shallows, and a step out at one corner.
    m.span(px0, px1, -1.2, -1.0, pz0, pz1, "sand", (0.5, 0.45, 0.35))
    m.span(px0, px1, -0.35, -0.3, pz0, pz1, "water", None, solid=False)
    m.span(-35.0, -33.0, 0.3, 0.5, pz0 - 1.0, pz1 + 1.0, "wood", (0.6, 0.45, 0.3))
    flight(m, px0, px0 + 1.4, pz0, pz0 + 1.4, -1.0, 0.0, "n", "dirt", None, thick=0.25)
    for (lx, lz) in ((-37.0, 31.0), (-31.0, 36.0), (-38.5, 38.0)):
        m.prop("nature_lily_large", lx, lz, y=-0.3)
    for (rx, rz, kind) in ((-41.5, 27.0, "nature_rock_large_a"), (-26.6, 39.0, "nature_rock_tall_a"), (-41.0, 41.0, "nature_grass_large"),
                           (-29.0, 26.6, "nature_grass_large"), (-26.0, 34.0, "nature_plant_bush_large")):
        m.prop(kind, rx, rz, (rx * 11) % 90)

    # Trees, a well, carts, woodpiles: the countryside between the houses.
    for (tx, tz, kind) in ((-38.0, -38.0, "nature_tree_pine_tall_a"), (-30.0, -38.0, "nature_tree_cone"), (10.0, -38.0, "nature_tree_oak"),
                           (38.0, -38.0, "nature_tree_default"), (38.0, -26.0, "nature_tree_fat"), (38.0, 14.0, "nature_tree_oak_fall"),
                           (38.0, 38.0, "nature_tree_default"), (14.0, 38.0, "nature_tree_tall"), (-14.0, 40.0, "nature_tree_detailed"),
                           (-40.0, 12.0, "nature_tree_oak"), (-40.0, -12.0, "nature_tree_pine_round_a"), (12.0, 14.0, "nature_tree_small"),
                           (-12.0, -14.0, "nature_tree_small"), (14.0, -14.0, "nature_tree_plateau")):
        m.prop(kind, tx, tz, (tx * 7 + tz) % 90)
    m.span(13.0, 15.0, 0.0, 0.9, 25.0, 27.0, "stone")
    m.span(12.9, 13.1, 0.9, 2.4, 25.9, 26.1, "wood", (0.5, 0.36, 0.25))
    m.span(14.9, 15.1, 0.9, 2.4, 25.9, 26.1, "wood", (0.5, 0.36, 0.25))
    m.span(12.7, 15.3, 2.4, 2.6, 25.5, 26.5, "roof")
    for (cx, cz, yaw) in ((-14.0, 14.0, 30.0), (16.0, -30.0, -20.0)):
        m.span(cx - 1.0, cx + 1.0, 0.5, 1.2, cz - 0.7, cz + 0.7, "wood", (0.55, 0.4, 0.28))
        m.prop("nature_log", cx + 1.6, cz, yaw)
    m.prop("nature_log_large", 34.0, -18.0, 0.0)
    m.prop("nature_log_stack", -34.0, -26.0, 0.0)
    m.prop("nature_sign", 0.0, -12.5, 0.0)

    for (x, z) in ((-6.0, -1.0), (6.0, -1.0), (-4.0, 4.0), (4.0, -4.0), (-4.0, -5.0), (4.0, 4.0), (0.0, -6.0), (0.0, 7.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((-3.5, 0.0), (3.5, 0.0), (0.0, -3.5), (0.0, 3.5)):
        m.spawn("seekers", x, z)
    return m.build()


def park():
    """Pinecrest Park: a playground of towers, a rope bridge and play tubes, a picnic shelter,
    the toilets, a tennis court, a pond with a footbridge, a ranger's cabin and the woods all
    round.

    The playground is the centre and the best place to be seen: towers to climb, tubes to
    crawl through, and the bridge under which a hider can crouch in the shallows. The woods'
    edge is where a hider goes to be patient."""
    m = Map(
        "hs_park", "Pinecrest Park",
        "A playground of towers and tubes, a pond, a tennis court and the woods. Crawl through the tubes.",
        {"sky_top": [0.30, 0.54, 0.90], "sky_horizon": [0.80, 0.88, 0.96], "ground": [0.34, 0.4, 0.26],
         "sun_pitch": -50, "sun_yaw": 20, "sun_energy": 1.0, "sun_colour": [1.0, 0.97, 0.9],
         "ambient": [0.62, 0.66, 0.72], "ambient_energy": 0.42, "fog": 0.0015},
    )
    # The field, with the pond left out of it.
    m.floor_around(-46, -42, 46, 42, (10.0, 18.0, 30.0, 32.0), "grass")
    boundary(m, -46, -42, 46, 42, height=2.6, mat="hedge", tint=(0.8, 1.0, 0.8))
    for (a, b, c, d) in ((-40, -1, -14, 1), (12, -1, 40, 1), (-1, -36, 1, -12), (-1, 9, 1, 36), (14, 1, 16, 16)):
        m.overlay(a, b, c, d, "dirt", (0.8, 0.72, 0.6))

    # The playground on rubber.
    m.overlay(-14, -12, 12, 9, "rubber", (0.72, 0.32, 0.25))
    wood = (0.62, 0.46, 0.32)
    def tower(x0, z0, x1, z1, y, roof=True):
        for px in (x0, x1):
            for pz in (z0, z1):
                m.span(px - 0.1, px + 0.1, 0.0, y + (2.2 if roof else 1.0), pz - 0.1, pz + 0.1, "wood", wood)
        m.span(x0, x1, y - 0.15, y, z0, z1, "wood", wood)
        if roof:
            m.span(x0 - 0.2, x1 + 0.2, y + 2.2, y + 2.35, z0 - 0.2, z1 + 0.2, "roof", (0.25, 0.45, 0.75))
        # Low walls on the sides, a gap on each.
        m.span(x0, x1, y, y + 0.9, z0 - 0.05, z0 + 0.05, "wood", (0.9, 0.75, 0.2))
    tower(-10.0, -8.0, -7.0, -5.0, 1.6)
    tower(-2.0, -8.0, 1.0, -5.0, 2.4)
    tower(5.0, 1.0, 8.0, 4.0, 1.6)
    flight(m, -9.8, -8.4, -5.0, -1.4, 0.0, 1.6, "n", "wood", wood, thick=0.12)
    flight(m, -0.6, 0.8, -5.0, 0.6, 0.0, 2.4, "n", "wood", wood, thick=0.12)
    flight(m, 6.4, 7.8, 4.0, 7.6, 0.0, 1.6, "n", "wood", wood, thick=0.12)
    # The rope bridge between the first two towers, climbing to the taller one: no rails.
    flight(m, -7.0, -2.0, -7.0, -6.0, 1.6, 2.4, "e", "wood", wood, thick=0.12)
    # Play tubes along the ground: crawl only.
    crawl(m, -6.0, -3.0, 4.0, -2.0, "x", mat="metal", tint=(0.85, 0.2, 0.2))
    crawl(m, -13.0, -10.0, -12.0, -5.0, "z", mat="metal", tint=(0.2, 0.45, 0.85))
    # Swings, a sandbox, a see-saw.
    for sx in (-12.5, -9.5):
        m.span(sx - 0.1, sx + 0.1, 0.0, 2.6, 3.0, 3.2, "metal")
    m.span(-12.6, -9.4, 2.5, 2.6, 2.95, 3.25, "metal")
    m.span(-12.6, -9.4, 2.5, 2.6, 6.75, 7.05, "metal")
    for sx in (-12.5, -9.5):
        m.span(sx - 0.1, sx + 0.1, 0.0, 2.6, 6.8, 7.0, "metal")
    for sx in (-11.8, -10.2):
        m.span(sx - 0.25, sx + 0.25, 0.45, 0.5, 4.8, 5.2, "wood", wood)
    m.overlay(-4.0, 3.0, 2.0, 7.0, "sand")
    for (a, b, c, d) in ((-4.0, 3.0, 2.0, 3.2), (-4.0, 6.8, 2.0, 7.0), (-4.0, 3.0, -3.8, 7.0), (1.8, 3.0, 2.0, 7.0)):
        m.span(a, c, 0.0, 0.3, b, d, "wood", wood)
    m.span(9.0, 12.0, 0.25, 0.35, -6.0, -5.6, "wood", (0.9, 0.75, 0.2))
    m.span(10.4, 10.6, 0.0, 0.3, -5.9, -5.7, "metal")
    m.prop("furniture_bench", -14.5, -2.0, 90.0)
    m.prop("furniture_bench", 12.5, 6.0, -90.0)
    m.prop("furniture_trashcan", -14.6, 0.0)

    # The picnic shelter, east.
    for px in (18.0, 24.0, 30.0):
        for pz in (-8.0, -2.0):
            m.span(px - 0.15, px + 0.15, 0.0, 3.0, pz - 0.15, pz + 0.15, "wood", wood)
    m.span(17.6, 30.4, 3.0, 3.2, -8.6, -1.4, "roof", (0.35, 0.3, 0.28))
    m.floor(18.0, -8.0, 30.0, -2.0, "concrete", None, y=0.08, thick=0.08)
    for tx in (21.0, 27.0):
        m.prop("furniture_table", tx, -5.0, 90.0, y=0.08)
        m.prop("furniture_bench", tx - 1.0, -5.0, 90.0, y=0.08)
        m.prop("furniture_bench", tx + 1.0, -5.0, -90.0, y=0.08)
    m.span(32.0, 33.2, 0.0, 1.0, -6.0, -4.8, "stone")
    m.prop("furniture_trashcan", 31.5, -2.5)

    # The toilets, north-east.
    tx0, tx1, tz0, tz1 = 30.0, 40.0, 12.0, 20.0
    m.room(tx0, tz0, tx1, tz1, "tile_small", None, height=2.8, lights=2, energy=1.0)
    m.wall((tx0, tz0), (tx1, tz0), "x", "brick", None, height=2.8, thick=OUTER, holes=[(1.0, 2.4, 0.0, DOOR_H), (7.6, 9.0, 0.0, DOOR_H)])
    m.wall((tx0, tz1), (tx1, tz1), "x", "brick", None, height=2.8, thick=OUTER)
    m.wall((tx0, tz0), (tx0, tz1), "z", "brick", None, height=2.8, thick=OUTER)
    m.wall((tx1, tz0), (tx1, tz1), "z", "brick", None, height=2.8, thick=OUTER)
    m.wall((35.0, tz0), (35.0, tz1), "z", "tile", None, height=2.8)
    bathroom(m, tx0 + 0.15, tz0 + 2.0, 34.9, tz1 - 0.15, stalls=3)
    bathroom(m, 35.1, tz0 + 2.0, tx1 - 0.15, tz1 - 0.15, stalls=3)

    # The tennis court, south-west, fenced, with a gate.
    cx0, cx1, cz0, cz1 = -40.0, -20.0, -36.0, -18.0
    m.overlay(cx0, cz0, cx1, cz1, "asphalt", (0.35, 0.55, 0.4))
    for (a, b, c, d) in ((cx0 + 1, cz0 + 1, cx1 - 1, cz0 + 1.1), (cx0 + 1, cz1 - 1.1, cx1 - 1, cz1 - 1), (cx0 + 1, cz0 + 1, cx0 + 1.1, cz1 - 1),
                         (cx1 - 1.1, cz0 + 1, cx1 - 1, cz1 - 1), (-30.05, cz0 + 1, -29.95, cz1 - 1)):
        m.overlay(a, b, c, d, "line")
    m.span(-30.05, -29.95, 0.0, 1.0, cz0 + 0.5, cz1 - 0.5, "fabric", (0.15, 0.15, 0.15))
    fence(m, cx0, cz0, cx1, cz1, height=3.0, mat="metal", tint=(0.4, 0.42, 0.45), gaps=[("e", 7.0, 9.0)])
    m.prop("furniture_bench", -18.6, -30.0, -90.0)
    m.prop("furniture_cardboard_box_open", -18.6, -32.0, 10.0)

    # The pond, with a footbridge over it high enough to crouch under in the shallows.
    m.span(10.0, 30.0, -1.2, -1.0, 18.0, 32.0, "sand", (0.5, 0.45, 0.35))
    m.span(10.0, 30.0, -0.3, -0.25, 18.0, 32.0, "water", None, solid=False)
    m.span(19.0, 21.0, 0.35, 0.5, 17.0, 33.0, "wood", wood)
    flight(m, 10.0, 11.4, 18.0, 21.0, -1.0, 0.0, "n", "dirt", (0.6, 0.55, 0.45), thick=0.25)
    flight(m, 28.6, 30.0, 29.0, 32.0, -1.0, 0.0, "s", "dirt", (0.6, 0.55, 0.45), thick=0.25)
    for (lx, lz) in ((14.0, 22.0), (25.0, 28.0), (16.0, 29.0), (27.0, 21.0)):
        m.prop("nature_lily_large", lx, lz, y=-0.25)
    for (rx, rz, kind) in ((8.0, 16.0, "nature_rock_large_a"), (31.0, 33.0, "nature_rock_tall_a"), (8.6, 26.0, "nature_grass_large"),
                           (31.4, 24.0, "nature_grass_large"), (9.0, 33.0, "nature_plant_bush_detailed")):
        m.prop(kind, rx, rz, (rx * 13) % 90)

    # The ranger's cabin, north-west, with a porch.
    rx0, rx1, rz0, rz1 = -38.0, -28.0, 14.0, 22.0
    m.room(rx0, rz0, rx1, rz1, "wood_floor", None, height=2.8, ceiling="wood", roof="roof", roof_tint=(0.35, 0.28, 0.22), lights=1)
    logs = (0.55, 0.38, 0.24)
    m.wall((rx0, rz0), (rx1, rz0), "x", "wood", logs, height=2.8, thick=OUTER, holes=[(4.3, 5.7, 0.0, DOOR_H), (1.0, 2.6, SILL, HEAD), (7.4, 9.0, SILL, HEAD)])
    m.wall((rx0, rz1), (rx1, rz1), "x", "wood", logs, height=2.8, thick=OUTER, holes=[(2.0, 3.4, 0.0, DOOR_H)])
    m.wall((rx0, rz0), (rx0, rz1), "z", "wood", logs, height=2.8, thick=OUTER, holes=[(3.0, 4.6, SILL, HEAD)])
    m.wall((rx1, rz0), (rx1, rz1), "z", "wood", logs, height=2.8, thick=OUTER, holes=[(3.0, 4.6, SILL, HEAD)])
    m.span(rx0, rx1, 0.0, 0.3, rz0 - 2.4, rz0, "wood", (0.6, 0.45, 0.3))
    m.against("furniture_bed_bunk", "e", rx1 - 0.15, 19.8)
    m.against("furniture_desk", "n", rz0 + 0.15, -35.5)
    m.prop("furniture_chair_desk", -35.5, 15.9, 180.0)
    m.against("furniture_bookcase_closed_wide", "s", rz1 - 0.15, -34.0)
    m.against("furniture_kitchen_stove", "w", rx0 + 0.15, 20.5)
    m.prop("furniture_lounge_chair", -30.5, 16.0, 200.0)
    m.prop("furniture_bench", -31.0, 12.6, 0.0, y=0.3)

    # The woods: a ring of trees, rocks, logs and bushes between the paths and the hedge.
    kinds = ["nature_tree_pine_tall_a", "nature_tree_default", "nature_tree_oak", "nature_tree_cone", "nature_tree_fat", "nature_tree_detailed"]
    tree_ring(m, -42.0, -38.0, 42.0, 38.0, 7.0, kinds, seed=5)
    undergrowth = [(-38.0, -6.5, "nature_rock_large_a"), (-37.5, 7.5, "nature_log_large"), (-36.0, -12.0, "nature_plant_bush_detailed"),
                   (-34.0, 4.0, "nature_stump_old"), (36.0, -14.0, "nature_rock_tall_a"), (40.0, -4.0, "nature_log_large"),
                   (38.0, 28.0, "nature_plant_bush_detailed"), (34.0, 36.0, "nature_stone_large_a"), (-6.0, 37.0, "nature_log_large"),
                   (3.0, -33.0, "nature_rock_large_a"), (-10.0, -34.0, "nature_plant_bush_detailed"), (-24.0, 34.0, "nature_plant_bush_large"),
                   (24.0, -36.0, "nature_stump_round"), (-16.0, 26.0, "nature_tree_oak"), (-6.0, 24.0, "nature_tree_default_fall"),
                   (38.0, -30.0, "nature_tree_plateau"), (-22.0, -8.0, "nature_tree_oak"), (-20.0, 6.0, "nature_tree_small"),
                   (-26.0, 26.0, "nature_rock_small_a"), (2.0, 26.0, "nature_plant_bush_large"), (28.0, 10.0, "nature_tree_oak_fall")]
    for (ux, uz, kind) in undergrowth:
        m.prop(kind, ux, uz, (ux * 17 + uz) % 120)

    for (x, z) in ((-6.0, 1.0), (-2.0, 1.0), (3.0, 4.0), (-8.0, 2.0), (-11.0, -11.0), (6.0, -8.0), (11.5, -1.0), (-4.0, -10.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((19.5, -7.2), (22.5, -7.2), (25.5, -7.2), (28.5, -7.2)):
        m.spawn("seekers", x, z)
    return m.build()


MAPS = {
    "hs_apartments": apartments,
    "hs_warehouse": warehouse,
    "hs_manor": manor,
    "hs_village": village,
    "hs_park": park,
}


def write_all(check):
    stale = []
    for mid, make in MAPS.items():
        doc = make()
        text = json.dumps(doc, indent=1, sort_keys=False) + "\n"
        path = os.path.join(ROOT, "maps", mid + ".json")
        if check:
            have = open(path).read() if os.path.exists(path) else ""
            if have != text:
                stale.append(mid)
            continue
        with open(path, "w") as f:
            f.write(text)
        print("%-14s %5d boxes %5d props %3d lights  %s" % (mid, len(doc["boxes"]), len(doc["props"]), len(doc["lights"]), doc["name"]))
    if check:
        if stale:
            print("stale: %s (run tools/build_maps.py)" % ", ".join(stale))
            return 1
        print("every map is what the builder writes")
    return 0


def sync_sizes():
    with open(GAME_CATALOGUE) as f:
        cat = json.load(f)
    sizes = {p["id"]: {"size": p["size"]} for p in cat["props"]}
    with open(SIZES_FILE, "w") as f:
        json.dump({"comment": "Prop sizes from game-hide-n-seek's props/catalogue.json, copied by tools/build_maps.py --sync-sizes. The builder stands props against walls and on each other with these; the game measures the models, this is a copy.", "props": sizes}, f, indent=1, sort_keys=True)
        f.write("\n")
    print("copied %d prop sizes" % len(sizes))


if __name__ == "__main__":
    if "--sync-sizes" in sys.argv:
        sync_sizes()
        sys.exit(0)
    sys.exit(write_all("--check" in sys.argv))
