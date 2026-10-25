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
    tools/build_maps.py --spots hs_park [y]   # the clearest open floor on a map, for placing toys

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
- A door is a hole in a wall with a fifth element, a dict of the door's options: `(3.0, 4.4,
  0.0, DOOR_H, {"name": "bedroom door"})`. The panel is cut to the hole, so the two cannot
  disagree. `{"secret": True}` is a panel of the wall's own material that slides into the wall:
  a passage nobody sees until somebody tries the wall. See `Map.door`.
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
GAME_PIECES = os.path.join(ROOT, "..", "game-hide-n-seek", "props", "pieces.json")

WALL = 0.2          # an interior wall
OUTER = 0.3         # an exterior wall, brick outside and paint inside
DOOR_W = 1.4
GROUND = -0.02      # the ground round the buildings, a hair under their floors (see Map.room)
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


def rest_on(under, dx, dz, pid, yaw):
    """How high above `under`'s base a prop `pid` set down at (dx, dz) from its middle comes to
    rest: the highest of the support's `tops` (the game's collision, sampled on a grid by its
    tools/measure_props.gd) under the prop's footprint, or the support's box top when it collides
    as its box."""
    s = SIZES[under["id"]]
    tops = s.get("tops")
    sx, sy, sz = s["size"]
    if not tops:
        return sy
    n = int(round(len(tops) ** 0.5))
    # Into the support's own frame: the map turns it by its yaw about +y (multiples of 90 here),
    # world = (x cos + z sin, -x sin + z cos), so local = (wx cos - wz sin, wx sin + wz cos).
    turn = int(round(under.get("yaw", 0.0))) % 360
    lx, lz = {0: (dx, dz), 90: (-dz, dx), 180: (-dx, -dz), 270: (dz, -dx)}.get(turn, (dx, dz))
    w, d = footprint(pid, (yaw - under.get("yaw", 0.0)))
    best = None
    nearest = None
    for k in range(n):
        for i in range(n):
            h = tops[k * n + i]
            if h is None:
                continue
            x = -sx / 2 + (i + 0.5) * sx / n
            z = -sz / 2 + (k + 0.5) * sz / n
            gap = max(abs(x - lx) - w / 2, abs(z - lz) - d / 2, 0.0)
            if gap <= 0.0 and (best is None or h > best):
                best = h
            if nearest is None or gap < nearest[0]:
                nearest = (gap, h)
    if best is None:
        best = nearest[1] if nearest else sy
    return best


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
        self.doors = []
        self.toys = []
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
        cuts = sorted([tuple(h[:4]) for h in holes], key=lambda h: h[0])
        doorways = set()
        for h in holes:
            if len(h) > 4 and h[4] is not None:
                doorways.add(tuple(h[:4]))
                opts = dict(h[4])
                # A pair of leaves, hung from either side of the hole and meeting in the middle.
                if opts.pop("leaves", 1) == 2:
                    mid = start + (h[0] + h[1]) / 2
                    self.door(along, start + h[0], mid, fixed, y0 + h[2], y0 + h[3], hinge="start",
                              wall_mat=mat, wall_tint=tint, wall_thick=thick, **opts)
                    self.door(along, mid, start + h[1], fixed, y0 + h[2], y0 + h[3], hinge="end",
                              wall_mat=mat, wall_tint=tint, wall_thick=thick, **opts)
                    continue
                self.door(along, start + h[0], start + h[1], fixed, y0 + h[2], y0 + h[3],
                          wall_mat=mat, wall_tint=tint, wall_thick=thick, **opts)
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
                if hb > 0.0 and (h0, h1, hb, ht) not in doorways:
                    self._wall_piece(along, start + h0, start + h1, fixed, y0 + hb, y0 + ht, 0.04, "glass", None)

    def _wall_piece(self, along, p0, p1, fixed, y0, y1, thick, mat, tint):
        if p1 - p0 <= 0.001 or y1 - y0 <= 0.001:
            return
        if along == "x":
            self.span(p0, p1, y0, y1, fixed - thick / 2, fixed + thick / 2, mat, tint)
        else:
            self.span(fixed - thick / 2, fixed + thick / 2, y0, y1, p0, p1, mat, tint)

    def door(self, along, p0, p1, fixed, y0, y1, name="", hinge="start", into="+", motion=None,
             secret=False, open=False, mat=None, tint=None, seconds=None, away=True, travel=None, angle=100.0,
             wall_mat="wood", wall_tint=None, wall_thick=WALL):
        """A door panel filling the hole from p0 to p1 (world, along the wall) and y0 to y1, in
        the wall along `along` at `fixed`.

        `hinge` is the end of the hole it hangs from ('start' is the smaller coordinate), `into`
        the side it swings to when it is opened from nowhere in particular ('+' is +z for an x
        wall, +x for a z wall); the game swings it away from whoever opens it unless `away` is
        False. `motion` is 'swing', 'slide' (along the wall, towards the hinge end) or 'lift'
        (up, a shutter). A secret door slides, is the wall's own material, has no handle, and is
        nearly the wall's thickness, so it reads as wall.
        """
        width, height = p1 - p0, y1 - y0
        mid = (p0 + p1) / 2
        if along == "x":
            at, yaw, side = (mid, y0 + height / 2, fixed), 0.0, (-1 if hinge == "start" else 1)
        else:
            # Turned 90 degrees, the panel's own +x is world -z: the start (smaller z) is its right.
            at, yaw, side = (fixed, y0 + height / 2, mid), 90.0, (1 if hinge == "start" else -1)
        # In a thick wall (a castle's, a tower's) a swinging door hangs on the face it opens
        # towards, square and one way only: in the middle of a deep opening it would swing into
        # the opening's own sides.
        sign_into = 1 if into == "+" else -1
        if wall_thick > 0.45 and not secret and (motion or "swing") == "swing":
            shift = sign_into * (wall_thick / 2 - 0.06)
            at = (at[0], at[1], at[2] + shift) if along == "x" else (at[0] + shift, at[1], at[2])
            away = False
            angle = min(angle, 90.0)
        if secret:
            motion = motion or "slide"
            thick = max(wall_thick - 0.03, 0.05)
            mat = mat or wall_mat
            tint = tint if tint is not None else wall_tint
        else:
            motion = motion or "swing"
            thick = 0.06
            mat = mat or "wood"
            tint = tint if tint is not None else (0.55, 0.4, 0.28)
        # The free edge's local z is side * width * sin(angle), and local z is the into-axis for
        # both kinds of wall, so the angle's sign is into times side.
        sign = (1 if into == "+" else -1) * side
        # A plain door has a finger's gap round it; a secret one a hair's, or its outline shows.
        gap = 0.006 if secret else 0.03
        d = {"at": v3(*at), "size": v3(width - gap, height - gap * 0.6, thick), "motion": motion,
             "hinge": "left" if side < 0 else "right", "mat": mat}
        if yaw:
            d["yaw"] = yaw
        if motion == "swing":
            d["angle"] = r(angle * sign)
            if not away:
                d["away"] = False
        if tint is not None:
            d["tint"] = [r(c) for c in tint]
        if name:
            d["name"] = name
        if secret:
            d["secret"] = True
        if open:
            d["open"] = True
        if seconds:
            d["seconds"] = seconds
        if travel:
            d["travel"] = travel
        self.doors.append(d)
        return d

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
        """A prop standing on top of another one, `under` a prop dict this built, resting on what
        is under its footprint there (`rest_on`): a pillow on a bed's mattress, not at the height
        of its headboard, which is what the bed's box top was."""
        top = under["at"][1] + rest_on(under, dx, dz, pid, yaw)
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
        building. A room beside a taller one wants less, or its roof shows inside the other.

        Its floor is at y 0 and the ground round a building is two centimetres lower (`GROUND`):
        at the same height, the two are drawn in stripes of each other. The ground goes down
        rather than the floor up, so that a rug laid on a floor is not laid under it."""
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
        # A shut door's panel is a wall to whoever spawns against it: an imported flat's start
        # stood a hand from a hinged door and was inside it once the importer hung all six.
        for d_ in self.doors:
            (dx, dy, dz), (sx, sy, sz) = d_["at"], d_["size"]
            if dy + sy / 2 < lo or dy - sy / 2 > hi:
                continue
            if d_.get("yaw"):
                yaw = math.radians(d_["yaw"])
                sx, sz = abs(sx * math.cos(yaw)) + abs(sz * math.sin(yaw)), abs(sx * math.sin(yaw)) + abs(sz * math.cos(yaw))
            if abs(x - dx) < sx / 2 + self.PERSON and abs(z - dz) < sz / 2 + self.PERSON:
                return "a door"
        return None

    def _floored(self, x, y, z, edge=0.1):
        for b in self.boxes:
            if b.get("solid", True) is False:
                continue
            (bx, by, bz), (sx, sy, sz) = b["at"], b["size"]
            if abs(by + sy / 2 - y) < 0.12 and abs(x - bx) < sx / 2 - edge and abs(z - bz) < sz / 2 - edge:
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

    # --- Toys ----------------------------------------------------------------------

    def toy(self, tid, x, z, yaw=0.0, y=0.0, links=None, on=None, near=4.0, group=None):
        """One of dot-props' toys (a beach ball, a swing, a switch...), placed by id. Settled
        at build (`_settle_toys`) to the nearest clear, floored spot within `near` metres, so a
        hint in the right part of the map is enough. Returns its index, which a switch's
        `links` names."""
        if tid not in TOYS:
            raise SystemExit("%s: no toy called %s (dot-props' stock: %s)" % (self.doc["id"], tid, ", ".join(sorted(TOYS))))
        t = {"toy": tid, "at": v3(x, y, z)}
        if yaw:
            t["yaw"] = r(yaw)
        if links:
            t["links"] = list(links)
        if on is not None:
            t["on"] = bool(on)
        t["_near"] = near
        if group is not None:
            # Moved together, by one offset: a rack of pins stays a rack.
            t["_group"] = group
        self.toys.append(t)
        return len(self.toys) - 1

    @staticmethod
    def _toy_rect(t, at=None):
        """A toy's footprint as an axis-aligned (x0, z0, x1, z1), turned by its yaw, with the
        room a person needs round it."""
        w, d, h, loose = TOYS[t["toy"]]
        w, d = TOY_CLEAR.get(t["toy"], (w, d))
        x, _, z = at if at is not None else t["at"]
        yaw = math.radians(t.get("yaw", 0.0))
        ww = abs(w * math.cos(yaw)) + abs(d * math.sin(yaw))
        dd = abs(w * math.sin(yaw)) + abs(d * math.cos(yaw))
        pad = 0.15 if loose else 0.6
        return (x - ww / 2 - pad, z - dd / 2 - pad, x + ww / 2 + pad, z + dd / 2 + pad)

    def _toy_clear(self, t, x, y, z, placed):
        """What a toy at (x, y, z) would stand in, or None: a prop, a solid box between its
        floor and its top, a spawn, another toy, or no floor under every corner."""
        w, d, h, loose = TOYS[t["toy"]]
        x0, z0, x1, z1 = self._toy_rect(t, (x, y, z))
        lo, hi = y + 0.06, y + h
        for p in self.props:
            sx, sy, sz = size_of(p["id"])
            yaw = math.radians(p.get("yaw", 0.0))
            pw = abs(sx * math.cos(yaw)) + abs(sz * math.sin(yaw))
            pd = abs(sx * math.sin(yaw)) + abs(sz * math.cos(yaw))
            py = p["at"][1]
            if py + sy < lo or py > hi:
                continue
            if p["at"][0] + pw / 2 > x0 and p["at"][0] - pw / 2 < x1 and p["at"][2] + pd / 2 > z0 and p["at"][2] - pd / 2 < z1:
                return p["id"]
        for b in self.boxes:
            if b.get("solid", True) is False:
                continue
            (bx, by, bz), (sx, sy, sz) = b["at"], b["size"]
            if by + sy / 2 <= lo or by - sy / 2 >= hi:
                continue
            if b.get("yaw"):
                yaw = math.radians(b["yaw"])
                sx, sz = abs(sx * math.cos(yaw)) + abs(sz * math.sin(yaw)), abs(sx * math.sin(yaw)) + abs(sz * math.cos(yaw))
            if bx + sx / 2 > x0 and bx - sx / 2 < x1 and bz + sz / 2 > z0 and bz - sz / 2 < z1:
                return "a %s box" % b["mat"]
        for d_ in self.doors:
            (dx, dy, dz) = d_["at"]
            if abs(dx - x) < (x1 - x0) / 2 + 1.6 and abs(dz - z) < (z1 - z0) / 2 + 1.6 and abs(dy - y) < 2.5:
                return "a door's swing"
        for side in self.spawns.values():
            for s in side:
                sx_, sy_, sz_ = s["at"]
                if abs(sy_ - y) < 1.0 and x0 - 0.6 < sx_ < x1 + 0.6 and z0 - 0.6 < sz_ < z1 + 0.6:
                    return "a spawn"
        for o in placed:
            ox0, oz0, ox1, oz1 = self._toy_rect(o)
            if abs(o["at"][1] - y) < 1.5 and ox1 > x0 and ox0 < x1 and oz1 > z0 and oz0 < z1:
                return "the %s" % o["toy"]
        pad = 0.15 if loose else 0.6
        for cx, cz in ((x, z), (x0 + pad, z0 + pad), (x1 - pad, z0 + pad), (x0 + pad, z1 - pad), (x1 - pad, z1 - pad)):
            if not self._floored(cx, y, cz, edge=0.0):
                return "no floor"
        return None

    def _settle_toys(self):
        """Moves every toy whose spot is taken to the nearest clear one within its `near`, and
        stops the build when there is none: a toy in a wall, on a spawn or over the pond is a
        map bug, and printing it is how the plan below gets fixed."""
        placed = []
        done = set()
        for i, t in enumerate(self.toys):
            if i in done:
                continue
            if "_group" in t:
                members = [k for k, o in enumerate(self.toys) if o.get("_group") == t["_group"]]
                self._settle_group(members, placed)
                done.update(members)
                continue
            near = t.pop("_near")
            x, y, z = t["at"]
            what = self._toy_clear(t, x, y, z, placed)
            if what is not None:
                found = None
                for ring in range(1, int(near / 0.5) + 1):
                    step = ring * 0.5
                    for k in range(8 * ring):
                        a = 2.0 * math.pi * k / (8 * ring)
                        cx, cz = x + step * math.cos(a), z + step * math.sin(a)
                        if self._toy_clear(t, cx, y, cz, placed) is None:
                            found = (cx, cz)
                            break
                    if found:
                        break
                if found is None:
                    raise SystemExit("%s: toy %d (%s) at %s is in %s and nothing within %.1f m is clear" % (self.doc["id"], i, t["toy"], t["at"], what, near))
                t["at"] = v3(found[0], y, found[1])
            # On the floor's top, not the hint's: the ground outside is a hair under 0.
            t["at"][1] = r(self._floor_top(t["at"][0], y, t["at"][2]))
            placed.append(t)

    def _settle_group(self, members, placed):
        group = [self.toys[k] for k in members]
        near = max(g.pop("_near") for g in group)
        for g in group:
            g.pop("_group")
        def clash(dx, dz):
            for g in group:
                x, y, z = g["at"]
                what = self._toy_clear(g, x + dx, y, z + dz, placed)
                if what is not None:
                    return what
            return None
        what = clash(0.0, 0.0)
        offset = (0.0, 0.0)
        if what is not None:
            offset = None
            for ring in range(1, int(near / 0.5) + 1):
                step = ring * 0.5
                for k in range(8 * ring):
                    a = 2.0 * math.pi * k / (8 * ring)
                    if clash(step * math.cos(a), step * math.sin(a)) is None:
                        offset = (step * math.cos(a), step * math.sin(a))
                        break
                if offset:
                    break
            if offset is None:
                raise SystemExit("%s: a set of %d %s at %s is in %s and nothing within %.1f m is clear" % (self.doc["id"], len(group), group[0]["toy"], group[0]["at"], what, near))
        for g in group:
            x, y, z = g["at"]
            g["at"] = v3(x + offset[0], y, z + offset[1])
            g["at"][1] = r(self._floor_top(g["at"][0], y, g["at"][2]))
            placed.append(g)

    def _floor_top(self, x, y, z):
        best = y
        for b in self.boxes:
            if b.get("solid", True) is False:
                continue
            (bx, by, bz), (sx, sy, sz) = b["at"], b["size"]
            top = by + sy / 2
            if abs(top - y) < 0.12 and abs(x - bx) < sx / 2 and abs(z - bz) < sz / 2:
                best = top if best == y else max(best, top)
        return best

    def spots(self, y=0.0, step=1.0, top=40):
        """The open floor at height y, clearest first: (clearance, x, z). For choosing where a
        toy goes (`--spots`)."""
        xs = [b["at"][0] for b in self.boxes] or [0.0]
        zs = [b["at"][2] for b in self.boxes] or [0.0]
        out = []
        x = min(xs)
        probe = {"toy": "_probe"}
        while x <= max(xs):
            z = min(zs)
            while z <= max(zs):
                if self._floored(x, y, z, edge=0.0):
                    for size in (6.0, 4.5, 3.0, 2.0, 1.2):
                        TOYS["_probe"] = (size, size, 2.0, True)
                        if self._toy_clear(probe | {"at": [x, y, z]}, x, y, z, []) is None:
                            out.append((size, r(x), r(z)))
                            break
                z += step
            x += step
        TOYS.pop("_probe", None)
        out.sort(key=lambda s: -s[0])
        return out[:top]

    def build(self):
        plan = TOY_PLANS.get(self.doc["id"])
        if plan is not None:
            plan(self)
        self._settle_spawns()
        self._face_spawns()
        self._settle_toys()
        self.doc["boxes"] = self.boxes
        self.doc["props"] = self.props
        self.doc["lights"] = self.lights
        self.doc["doors"] = self.doors
        self.doc["spawns"] = self.spawns
        if self.toys:
            self.doc["toys"] = self.toys
        return self.doc


# --- Furnishing ------------------------------------------------------------------

def bathroom(m, x0, z0, x1, z1, stalls=2, doors=True, y=0.0):
    """Stalls with toilets along the back, sinks along the side. With `doors`, every stall has
    one, which opens outward only (inward, the toilet is in the way) and stops a hand's width
    off the floor, as a stall's does."""
    stall_w = 1.5
    for i in range(stalls):
        sx = x0 + 0.2 + i * stall_w
        m.prop("furniture_toilet", sx + stall_w / 2, z1 - 0.6, 180.0, y=y)
        if i > 0:
            m.span(sx - 0.03, sx + 0.03, y + 0.15, y + 2.0, z1 - 1.7, z1, "metal", (0.75, 0.82, 0.88))
        if doors:
            m.door("x", sx + 0.03 if i > 0 else sx, sx + stall_w - 0.03, z1 - 1.7, y + 0.15, y + 2.0, name="stall door",
                   hinge="start", into="-", away=False, angle=90.0, mat="metal", tint=(0.62, 0.72, 0.8))
    m.span(x0 + 0.2 + stalls * stall_w - 0.03, x0 + 0.2 + stalls * stall_w + 0.03, y + 0.15, y + 2.0, z1 - 1.7, z1, "metal", (0.75, 0.82, 0.88))
    for i in range(2):
        m.against("furniture_bathroom_sink_square", "e", x1, z0 + 1.0 + i * 1.2, y=y)
    m.prop("furniture_trashcan", x1 - 0.4, z0 + 0.2, y=y)
    m.against("furniture_bathroom_cabinet", "w", x0, z0 + 0.8, y=y)


def lockers(m, x0, x1, z, side, height=2.0, y=0.0):
    """A row of lockers against a corridor wall: part of the wall, not something to hide as."""
    depth = 0.45
    if side == "n":
        m.span(x0, x1, y, y + height, z, z + depth, "locker")
    else:
        m.span(x0, x1, y, y + height, z - depth, z, "locker")


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


def fence(m, x0, z0, x1, z1, height=1.6, mat="wood", tint=(0.62, 0.48, 0.34), gaps=(), skip=(), gate=None):
    """A fence round an area, as thin walls with gates (gaps: (side, from, to)), leaving out the
    sides in `skip`: a garden's side along its house, which is the house's wall. `gate` is a
    door's options, hung in every gap; without it a gap is just a gap."""
    sides = {
        "n": ((x0, z0), (x1, z0), "x"),
        "s": ((x0, z1), (x1, z1), "x"),
        "w": ((x0, z0), (x0, z1), "z"),
        "e": ((x1, z0), (x1, z1), "z"),
    }
    for side, (a, b, along) in sides.items():
        if side in skip:
            continue
        holes = [(g0, g1, 0.0, height, dict(gate, mat=gate.get("mat", mat), tint=gate.get("tint", tint)) if gate else None)
                 for gs, g0, g1 in gaps if gs == side]
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


def container(m, x0, z0, along, y=0.0, open_end=None, tint=(0.62, 0.22, 0.18), length=6.0, width=2.4, height=2.6,
              doors="shut"):
    """A shipping container with its near corner at (x0, z0) running `along` x or z, solid
    everywhere except `open_end` ('w'/'e' for one along x, 'n'/'s' along z), which is a pair of
    doors: a room you can hide at the back of and shut yourself into. `doors` is 'shut', 'open'
    or None for an end with no doors at all. The leaves swing outward only, as a container's
    do. Its roof is walkable."""
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
    if open_end and doors:
        leaf = {"name": "container door", "away": False, "open": doors == "open", "mat": "metal",
                "tint": tuple(c * 0.8 for c in tint)}
        if open_end in ("w", "e"):
            at, into, a, b = (x0 + t / 2 if open_end == "w" else x1 - t / 2), ("-" if open_end == "w" else "+"), z0 + t, z1 - t
            wall_along = "z"
        else:
            at, into, a, b = (z0 + t / 2 if open_end == "n" else z1 - t / 2), ("-" if open_end == "n" else "+"), x0 + t, x1 - t
            wall_along = "x"
        mid = (a + b) / 2
        m.door(wall_along, a, mid, at, y + 0.15, y + height - t, hinge="start", into=into, **leaf)
        m.door(wall_along, mid, b, at, y + 0.15, y + height - t, hinge="end", into=into, **leaf)
    return (x0, z0, x1, z1)


def car(m, x, z, along, tint):
    """A parked car: a body and a cabin, solid, a little over a metre high. Crouch behind it."""
    if along == "x":
        m.span(x - 2.1, x + 2.1, 0.2, 1.0, z - 0.85, z + 0.85, "metal", tint)
        m.span(x - 1.0, x + 0.9, 1.0, 1.5, z - 0.75, z + 0.75, "glass", None)
    else:
        m.span(x - 0.85, x + 0.85, 0.2, 1.0, z - 2.1, z + 2.1, "metal", tint)
        m.span(x - 0.75, x + 0.75, 1.0, 1.5, z - 1.0, z + 0.9, "glass", None)


def stall(m, x, z, cloth, y=0.0):
    """A market stall: a counter, four posts, a cloth roof, boxes under the counter."""
    m.span(x - 1.3, x + 1.3, y, y + 0.95, z - 0.5, z + 0.5, "wood", (0.66, 0.5, 0.34))
    for dx in (-1.35, 1.35):
        for dz in (-0.55, 1.6):
            m.span(x + dx - 0.06, x + dx + 0.06, y, y + 2.4, z + dz - 0.06, z + dz + 0.06, "wood", (0.55, 0.4, 0.28))
    m.span(x - 1.55, x + 1.55, y + 2.4, y + 2.5, z - 0.75, z + 1.8, "fabric", cloth)
    m.prop("furniture_cardboard_box_open", x - 0.6, z + 1.0, 10.0, y=y)
    m.prop("furniture_cardboard_box_closed", x + 0.7, z + 1.1, -15.0, y=y)


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
    m.floor(-32, -28, 24, 24, "concrete", (0.82, 0.82, 0.8), y=GROUND)
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
            # The front door stands open: the street is where the seekers count.
            south_holes = [(1.0, 2.8, SILL, HEAD), (9.5, 11.5, 0.0, DOOR_H, {"name": "front door", "into": "-", "open": True}), (18.5, 20.3, SILL, HEAD)]
            north_holes = [(1.0, 2.4, 0.0, DOOR_H, {"name": "back door", "into": "+"}), (13.5, 15.3, SILL, HEAD)]
            west_holes = [(5.0, 6.4, SILL, HEAD)]
            east_holes = [(11.2, 12.6, 0.0, DOOR_H, {"name": "side door", "into": "-"})]
        # A fire door opens outward and only outward, onto the escape.
        fire = {"name": "fire door", "into": "-", "away": False, "mat": "metal", "tint": (0.5, 0.15, 0.12)}
        if k == 1:
            west_holes = [(7.0, 8.4, 0.0, DOOR_H, fire), (11.5, 12.9, SILL, HEAD)]     # to the fire escape
        if k == 2:
            west_holes = [(5.0, 6.4, SILL, HEAD), (15.8, 17.0, 0.0, DOOR_H, fire)]     # to the fire escape
        m.wall((X0, Z1), (X1, Z1), "x", "brick", None, height=H, y0=y, thick=OUTER, holes=south_holes, inner="plaster", inner_tint=plaster)
        m.wall((X0, Z0), (X1, Z0), "x", "brick", None, height=H, y0=y, thick=OUTER, holes=north_holes)
        m.wall((X0, Z0), (X0, Z1), "z", "brick", None, height=H, y0=y, thick=OUTER, holes=west_holes)
        m.wall((X1, Z0), (X1, Z1), "z", "brick", None, height=H, y0=y, thick=OUTER, holes=east_holes, inner=None)

        # The corridor between z -1 and 1.5, the stairwell's wall at x 6, the flats either side.
        # Every flat's own front door, off the corridor; the ground floor's middle south one is
        # the lobby, which has none.
        flat = {"name": "flat door", "into": "-"}
        south = {"name": "flat door", "into": "+"}
        north_doors = [(3.0, 4.4, 0.0, DOOR_H, flat), (14.0, 15.4, 0.0, DOOR_H, flat)]
        m.wall((X0, -1.0), (6.0, -1.0), "x", "paint", plaster, height=H, y0=y, holes=north_doors)
        m.wall((6.0, Z0), (6.0, -1.0), "z", "paint", plaster, height=H, y0=y)
        m.wall((X0, 1.5), (X1, 1.5), "x", "paint", plaster, height=H, y0=y,
               holes=[(4.0, 5.4, 0.0, DOOR_H, south), (12.0, 13.4, 0.0, DOOR_H, None if k == 0 else south),
                      (21.0, 22.4, 0.0, DOOR_H, south)])
        # Between the two north-west bedrooms on the top floor, a panel of wall that slides: the
        # one way from one flat to the next that is not the corridor.
        m.wall((-5.0, Z0), (-5.0, -1.0), "z", "paint", plaster, height=H, y0=y,
               holes=[(1.2, 2.2, 0.0, 2.0, {"name": "loose panel", "secret": True, "hinge": "start"})] if k == 2 else ())
        m.wall((-7.0, 1.5), (-7.0, Z1), "z", "paint", plaster, height=H, y0=y)
        m.wall((1.5, 1.5), (1.5, Z1), "z", "paint", plaster, height=H, y0=y)
        # In each north flat, a wall between the living room and the bedroom, with a door.
        # (The east flat's bedroom is through its bathroom.)
        bed = {"name": "bedroom door", "into": "-"}
        m.wall((X0, -6.5), (-5.0, -6.5), "x", "paint", plaster, height=H, y0=y, holes=[(7.0, 8.4, 0.0, DOOR_H, bed)])
        m.wall((-5.0, -6.5), (6.0, -6.5), "x", "paint", plaster, height=H, y0=y, holes=[(7.5, 8.9, 0.0, DOOR_H, bed)])

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
        m.wall((1.5, Z0), (1.5, -6.5), "z", "paint", plaster, height=H, y0=y,
               holes=[(2.0, 3.4, 0.0, DOOR_H, {"name": "bathroom door", "into": "+", "tint": (0.9, 0.9, 0.88)})])
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
        # A low wall along the lower flight's side, under the upper one: the slab steps are
        # 0.42 m off the floor there, a hair over a step, and the mesh joined them anyway, so a
        # stand-in under the upper flight tried to step up onto the second step from the side
        # for twenty seconds (game-hide-n-seek's headless_bots).
        m.span(8.0, 8.1, y, y + 1.0, -6.5, -2.0, "concrete", (0.75, 0.75, 0.74))
    # The hut on the roof over the stairwell, with its door to the roof.
    m.wall((6.0, Z0), (6.0, -1.0), "z", "brick", None, height=2.8, y0=TOP)
    m.wall((X1, Z0), (X1, -1.0), "z", "brick", None, height=2.8, y0=TOP)
    m.wall((6.0, Z0), (X1, Z0), "x", "brick", None, height=2.8, y0=TOP)
    m.wall((6.0, -1.0), (X1, -1.0), "x", "brick", None, height=2.8, y0=TOP,
           holes=[(0.5, 2.0, 0.0, DOOR_H, {"name": "roof door", "into": "+", "mat": "metal", "tint": (0.35, 0.4, 0.45)})])
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
    m.wall((-6.0, 4.5), (-1.0, 4.5), "x", "wood", (0.6, 0.45, 0.3), height=2.2, y0=TOP, thick=0.12,
           # Hung from its east end, so it opens against the empty half: from the west end it
           # closed off the boxes' corner, and a hider going behind it stood at it ten seconds
           # (game-hide-n-seek's headless_bots).
           holes=[(1.8, 3.0, 0.0, 2.0, {"name": "shed door", "into": "+", "hinge": "end", "tint": (0.45, 0.32, 0.22)})])
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
    # The low wall stops short of the back door: it stood 1.2 m off the building, so the door
    # opened out spanned the strip between them and left nobody a way round its panel
    # (game-hide-n-seek's headless_bots, a stand-in at it for fourteen seconds).
    m.span(-12.5, 9.0, 0.0, 0.9, -13.5, -13.2, "brick")

    # The corner shop to the east: aisles of shelves, a counter, a back room.
    sx0, sx1, sz0, sz1 = 13.0, 22.0, -6.0, 6.0
    m.room(sx0, sz0, sx1, sz1, "tile_check", None, height=3.2, lights=1, energy=1.2)
    m.wall((sx0, sz0), (sx1, sz0), "x", "brick", None, thick=OUTER, inner="paint", inner_tint=(0.95, 0.95, 0.9))
    m.wall((sx0, sz1), (sx1, sz1), "x", "brick", None, thick=OUTER,
           holes=[(1.0, 3.0, SILL, HEAD), (4.0, 5.6, 0.0, DOOR_H, {"name": "shop door", "into": "+", "away": False, "open": True, "tint": (0.2, 0.35, 0.3)}), (6.6, 8.4, SILL, HEAD)])
    m.wall((sx0, sz0), (sx0, sz1), "z", "brick", None, thick=OUTER, holes=[(9.0, 10.4, 0.0, DOOR_H, {"name": "shop's side door", "into": "+"})])
    m.wall((sx1, sz0), (sx1, sz1), "z", "brick", None, thick=OUTER)
    m.wall((sx0, -2.5), (sx1, -2.5), "x", "paint", (0.95, 0.95, 0.9), holes=[(6.5, 7.9, 0.0, DOOR_H, {"name": "stock room door", "into": "-"})])
    # The aisles start 3 m in from the side door, and the desk is clear of its north half: the
    # door opened inward lies along the aisle's end, and with the aisles at 2.5 m and the desk
    # in front of the doorway its free edge left 0.7 m either side, so a stand-in coming in
    # stood at it for fourteen seconds (game-hide-n-seek's headless_bots).
    for az in (0.0, 2.6):
        for i in range(4):
            m.prop("furniture_kitchen_bar", 16.0 + i * 1.0, az, 0.0)
            m.prop("furniture_kitchen_bar", 16.0 + i * 1.0, az + 0.5, 180.0)
    m.prop("furniture_kitchen_fridge_large", sx1 - 0.65, 1.0, -90.0)
    m.prop("furniture_bear", 18.0, 4.8, 200.0)
    m.prop("furniture_kitchen_fridge_large", sx1 - 0.65, 2.3, -90.0)
    m.prop("furniture_desk", 15.4, 5.1, 0.0)
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
    m.floor(-36, -30, 30, 30, "concrete", (0.78, 0.78, 0.76), y=GROUND)
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
           holes=[(6.0, 10.0, 0.0, 4.5, {"name": "loading bay shutter", "motion": "lift", "travel": 3.3, "mat": "metal", "tint": (0.5, 0.52, 0.5), "seconds": 1.6, "open": True}),
                  (16.0, 20.0, 0.0, 4.5, {"name": "loading bay shutter", "motion": "lift", "travel": 3.3, "mat": "metal", "tint": (0.5, 0.52, 0.5), "seconds": 1.6}),
                  (30.0, 31.4, 0.0, DOOR_H, {"name": "side door", "into": "+", "mat": "metal", "tint": (0.3, 0.4, 0.55)})])
    m.wall((HX0, HZ0), (HX0, HZ1), "z", "metal", steel, height=HH, thick=OUTER, holes=[(10.0, 13.0, 5.6, 6.8)])
    m.wall((HX1, HZ0), (HX1, HZ1), "z", "metal", steel, height=HH, thick=OUTER,
           holes=[(6.0, 7.4, 0.0, DOOR_H, {"name": "fire door", "into": "+", "away": False, "mat": "metal", "tint": (0.5, 0.15, 0.12)}), (14.0, 17.0, 5.6, 6.8)])
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
           holes=[(1.0, 3.0, 1.0, 2.0), (4.2, 5.6, 0.0, DOOR_H, {"name": "office door", "into": "-", "away": False}), (6.5, 8.5, 1.0, 2.0)])
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
    container(m, -30.0, 14.0, "x", open_end="e", tint=blue, doors="open")
    container(m, -30.0, 14.0, "x", y=2.6, tint=red)
    container(m, -30.0, 17.0, "x", open_end="e", tint=green)
    container(m, -30.0, 22.0, "x", tint=grey)
    container(m, -20.0, 20.0, "x", open_end="w", tint=red)
    flight(m, -14.0, -10.0, 20.1, 21.3, 0.0, 2.6, "w", "metal", (0.3, 0.3, 0.32), thick=0.12)
    container(m, -6.0, 18.0, "z", open_end="s", tint=green, doors="open")
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
    m.wall((20.0, -18.0), (26.0, -18.0), "x", "brick", None, height=2.8, thick=OUTER,
           holes=[(0.6, 2.0, 0.0, DOOR_H, {"name": "gatehouse door", "into": "+"}), (3.0, 5.0, SILL, HEAD)])
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

    m.floor(-46, -20, 42, 28, "grass", y=GROUND)
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

    # The cupboard under the stair, where the flight is high enough to stand in (z -6 to -3):
    # its sides follow the steps' undersides, and the way in is a panel of the stair's own
    # wood at its back, on the hall side, that slides. Low and dark, and only a hider who has
    # tried every wall knows it is there.
    steps = int(math.ceil(UP / STEP))
    for i in range(steps):
        lo, hi = 2.0 - 8.0 * (i + 1) / steps, 2.0 - 8.0 * i / steps
        lo, hi = max(lo, -6.0), min(hi, -3.0)
        if hi - lo <= 0.001:
            continue
        under = UP * (i + 1) / steps - 0.3
        m.span(-1.5, -1.4, 0.0, under, lo, hi, "wood", panel)
        m.span(1.4, 1.5, 0.0, under, lo, hi, "wood", panel)
    m.wall((-1.4, -3.0), (1.4, -3.0), "x", "wood", panel, height=2.2)
    m.wall((-1.5, -6.0), (1.5, -6.0), "x", "wood", panel, height=UP - 0.25,
           holes=[(0.75, 1.75, 0.0, 2.1, {"name": "panel", "secret": True, "hinge": "end"})])
    m.prop("furniture_cardboard_box_closed", -0.9, -3.6, 12.0)
    m.prop("furniture_cardboard_box_open", 0.8, -3.7, -8.0)

    for k, y in enumerate((0.0, UP)):
        # Outer walls: stone outside, wallpaper in.
        if k == 0:
            south = [(2.0, 3.8, SILL, HEAD), (6.5, 8.3, SILL, HEAD), (13.3, 14.7, 0.0, DOOR_H, {"name": "front door", "into": "-", "open": True, "tint": (0.35, 0.2, 0.15)}),
                     (19.7, 21.5, SILL, HEAD), (24.2, 26.0, SILL, HEAD)]
            north = [(3.0, 4.8, SILL, HEAD), (13.3, 14.7, 0.0, DOOR_H, {"name": "garden door", "into": "+"}), (22.0, 23.8, SILL, HEAD)]
        else:
            south = [(2.0, 3.8, SILL, HEAD), (6.5, 8.3, SILL, HEAD), (12.6, 15.4, 1.0, 3.0), (19.7, 21.5, SILL, HEAD), (24.2, 26.0, SILL, HEAD)]
            north = [(3.0, 4.8, SILL, HEAD), (12.6, 15.4, 1.0, 3.0), (22.0, 23.8, SILL, HEAD)]
        m.wall((X0, Z1), (X1, Z1), "x", "stone", None, height=H, y0=y, thick=OUTER, holes=south, inner="paint", inner_tint=wallpaper)
        m.wall((X0, Z0), (X1, Z0), "x", "stone", None, height=H, y0=y, thick=OUTER, holes=north)
        side_door = {"name": "side door", "into": "+"}
        m.wall((X0, Z0), (X0, Z1), "z", "stone", None, height=H, y0=y, thick=OUTER,
               holes=[(3.0, 4.8, SILL, HEAD), (12.0, 13.4, 0.0, DOOR_H, side_door) if k == 0 else (12.0, 13.8, SILL, HEAD)])
        m.wall((X1, Z0), (X1, Z1), "z", "stone", None, height=H, y0=y, thick=OUTER,
               holes=[(3.0, 4.8, SILL, HEAD), (12.0, 13.4, 0.0, DOOR_H, dict(side_door, into="-")) if k == 0 else (12.0, 13.8, SILL, HEAD)])
        # The wings' inner walls on the hall, with doors, and the walls between rooms. Every
        # room has a door, and every one of them starts shut.
        rooms = [["dining room", "library"], ["kitchen", "lounge"]] if k == 0 else [["bedroom", "bedroom"], ["bedroom", "bathroom"]]
        # Upstairs they open into the rooms only: the gallery outside is 1.4 m wide, the width
        # of a door, so one opened from inside lay right across it and a stand-in on the gallery
        # stood at it for up to thirteen seconds (game-hide-n-seek's headless_bots).
        wing = {"tint": panel} if k == 0 else {"tint": panel, "away": False}
        for wx, names, into in ((-4.0, rooms[0], "-"), (4.0, rooms[1], "+")):
            m.wall((wx, Z0), (wx, Z1), "z", "paint", wallpaper, height=H, y0=y,
                   holes=[(3.0, 4.4, 0.0, DOOR_H, dict(wing, name=names[0] + " door", into=into)),
                          (13.0, 14.4, 0.0, DOOR_H, dict(wing, name=names[1] + " door", into=into))])
        # Through-doors between the two ground-floor rooms of each wing; upstairs every room
        # opens on the gallery alone.
        # (The lounge's door is at the hall end of its wall: the sofa is against the rest.)
        through = {"name": "door", "into": "+", "away": False, "tint": panel}
        m.wall((X0, -1.0), (-4.0, -1.0), "x", "paint", wallpaper, height=H, y0=y, holes=[(4.0, 5.4, 0.0, DOOR_H, through)] if k == 0 else [])
        m.wall((4.0, -1.0), (X1, -1.0), "x", "paint", wallpaper, height=H, y0=y, holes=[(0.6, 2.0, 0.0, DOOR_H, dict(through, into="-"))] if k == 0 else [])
        for (a, b, c, d) in ((X0, Z0, -4.0, -1.0), (X0, -1.0, -4.0, Z1), (4.0, Z0, X1, -1.0), (4.0, -1.0, X1, Z1)):
            cx, cz = (a + c) / 2, (b + d) / 2
            m.lamp_panel(cx, cz, y + H)
            m.light(cx, y + H - 0.5, cz, rng=10.0, energy=1.0, colour=(1.0, 0.92, 0.8))

    # Ground floor: dining room, library, kitchen, lounge; the hall's furniture.
    # The dining table a metre in from the through-door to the library: with its end chairs
    # 1.2 m from the doorway, the way on turned hard at the door's frame, and a stand-in coming
    # through caught that frame for eleven to thirteen seconds (game-hide-n-seek's headless_bots).
    table = m.prop("furniture_table_cloth", -9.0, -6.5, 90.0)
    m.prop("furniture_table_cloth", -9.0, -4.5, 90.0)
    for dz in (-7.3, -6.0, -4.7, -3.4):
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
    m.wall((gx0, gz1), (gx1, gz1), "x", "glass", None, height=3.0, thick=0.06,
           holes=[(5.3, 6.7, 0.0, DOOR_H, {"name": "greenhouse door", "into": "+", "mat": "glass", "tint": (1.0, 1.0, 1.0)})])
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
    m.wall((34.0, 12.0), (34.0, 18.0), "z", "wood", (0.5, 0.38, 0.28), height=2.6, thick=0.12,
           holes=[(2.0, 3.4, 0.0, DOOR_H, {"name": "shed door", "into": "-", "away": False, "tint": (0.42, 0.3, 0.2)})])
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
    m.floor_around(-44, -44, 44, 44, (px0, pz0, px1, pz1), "grass", y=GROUND)
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
        inward = "+" if face == "n" else "-"
        painted = [(0.3, 0.42, 0.6), (0.55, 0.2, 0.18), (0.25, 0.45, 0.32), (0.85, 0.75, 0.4), (0.3, 0.3, 0.32), (0.5, 0.36, 0.5)][idx % 6]
        # The front door is the living room's, a metre clear of the partition: at 3.8-5.2 the
        # partition met the front wall inside the doorway, and a stand-in going for the bedroom
        # stood in the 1 m left between them (game-hide-n-seek's headless_bots). Hung from the
        # partition's end, so opened out it clears the next garden's fence post.
        front = [(0.6, 2.0, SILL, HEAD), (2.6, 4.0, 0.0, DOOR_H, {"name": "front door", "into": inward, "hinge": "end", "tint": painted}), (6.4, 8.0, SILL, HEAD)]
        # The back door opens out, into the garden: inward, the stove is behind it. 2 m from the
        # corner, not 1: its open panel ended 0.71 m from the garden fence, one body's width, and
        # a stand-in coming out wedged there for eighteen seconds (game-hide-n-seek's headless_bots).
        back = [(2.0, 3.4, 0.0, DOOR_H, {"name": "back door", "into": inward, "away": False}), (6.4, 8.0, SILL, HEAD)]
        m.wall((x0, z0), (x1, z0), "x", "plaster", tint, height=2.8, thick=OUTER, holes=front if face == "n" else back)
        m.wall((x0, z1), (x1, z1), "x", "plaster", tint, height=2.8, thick=OUTER, holes=front if face == "s" else back)
        m.wall((x0, z0), (x0, z1), "z", "plaster", tint, height=2.8, thick=OUTER, holes=[(2.6, 4.2, SILL, HEAD)])
        m.wall((x1, z0), (x1, z1), "z", "plaster", tint, height=2.8, thick=OUTER, holes=[(2.6, 4.2, SILL, HEAD)])
        # The bedroom door slides into the partition: swung into a bedroom 4 m across, its open
        # panel left the way round it between its edge and the bed, and stand-ins lingered beside
        # it for ten to fourteen seconds (game-hide-n-seek's headless_bots).
        m.wall((cx + 0.5, z0), (cx + 0.5, z1), "z", "paint", (0.95, 0.93, 0.88), height=2.8,
               holes=[(2.8, 4.2, 0.0, DOOR_H, {"name": "bedroom door", "motion": "slide"})])
        # Living room and kitchen west of the partition, the bedroom east.
        m.prop("furniture_lounge_sofa", x0 + 1.5, cz, 90.0)
        m.prop("furniture_table_round", cx - 1.4, cz + (1.2 if face == "n" else -1.2))
        m.prop("furniture_chair", cx - 1.4, cz + (2.1 if face == "n" else -2.1), 180.0 if face == "n" else 0.0)
        m.against("furniture_kitchen_stove", "w", x0 + 0.15, cz + (2.5 if face == "n" else -2.5))
        m.against("furniture_bookcase_closed", "w", x0 + 0.15, cz + (-2.4 if face == "n" else 2.4))
        # Against the far wall, out of the doorway's way.
        m.prop("furniture_bed_single", x1 - 0.85, cz, 0.0)
        m.against("furniture_bookcase_closed_doors", "e", x1 - 0.15, cz + (2.6 if face == "n" else -2.6))
        m.prop("furniture_cabinet_bed_drawer", cx + 1.2, cz + (-2.8 if face == "n" else 2.8))
        # The garden behind: a low fence with a gate, a vegetable patch or a woodpile.
        gz0, gz1 = (z1, z1 + 6.0) if face == "n" else (z0 - 6.0, z0)
        # A west gate opens into the garden, so it is at the garden's far end, clear of the back
        # door's panel, and the crops are clear of it: at 2 m from the house, the two open panels
        # left 0.6 m between them and a melon by the gate's edge, and a stand-in going for the
        # gate lingered there for eleven seconds (game-hide-n-seek's headless_bots).
        gate_at = (3.6, 5.0) if face == "n" else (1.0, 2.4)
        fence(m, x0, gz0, x1, gz1, height=1.1, gaps=[("w",) + gate_at] if idx % 2 else [("e", 2.0, 3.4)],
              skip=("n" if face == "n" else "s",), gate={"name": "garden gate", "into": "+"})
        gm = (gz0 + gz1) / 2
        if idx % 2:
            for i in range(3):
                m.prop("nature_crop_pumpkin" if i % 2 else "nature_crop_melon", cx - 1.5 + i * 2.0, gm)
        else:
            m.prop("nature_log_stack", cx - 2.0, gm, 90.0)
            # Clear of the east gate's swing: opened from outside it swings in, and at cx + 2.4
            # its edge met this bush (game-hide-n-seek's headless_bots, eighteen seconds).
            m.prop("nature_plant_bush_detailed", cx + 1.0, gm)

    for i, (cx, cz, face) in enumerate(((-22.0, -20.0, "s"), (6.0, -24.0, "s"), (22.0, -20.0, "s"),
                                       (-22.0, 20.0, "n"), (24.0, 20.0, "n"), (-28.0, -6.0, "s"))):
        cottage(cx, cz, face, i)

    # The barn, east: a hayloft over the back half, stairs up to it, stalls and bales below.
    bx0, bx1, bz0, bz1 = 20.0, 34.0, -7.0, 7.0
    BH = 6.0
    m.floor(bx0, bz0, bx1, bz1, "dirt", (0.8, 0.7, 0.55), y=0.02, thick=0.02)
    m.ceiling(bx0, bz0, bx1, bz1, BH, "wood", (0.6, 0.45, 0.3), "roof", (0.5, 0.2, 0.18), 0.4)
    red = (0.62, 0.22, 0.18)
    barn_door = {"name": "barn door", "into": "+", "tint": (0.5, 0.18, 0.15)}
    m.wall((bx0, bz0), (bx1, bz0), "x", "wood", red, height=BH, thick=OUTER, holes=[(5.0, 6.4, 0.0, DOOR_H, dict(barn_door, into="-"))])
    m.wall((bx0, bz1), (bx1, bz1), "x", "wood", red, height=BH, thick=OUTER, holes=[(9.0, 10.4, 0.0, DOOR_H, barn_door)])
    # The big doors on the square, a pair of leaves that swing out and stand open; and the
    # hayloft's pair, which swing out over a three-metre drop.
    leaf = {"leaves": 2, "away": False, "mat": "wood", "tint": (0.5, 0.18, 0.15)}
    m.wall((bx0, bz0), (bx0, bz1), "z", "wood", red, height=BH, thick=OUTER,
           holes=[(5.0, 9.0, 0.0, 4.0, dict(leaf, name="barn door", into="-", open=True))])
    m.wall((bx1, bz0), (bx1, bz1), "z", "wood", red, height=BH, thick=OUTER,
           holes=[(5.5, 8.5, 3.6, 5.0, dict(leaf, name="hayloft door", into="+"))])
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
    m.wall((hx0, hz0), (hx1, hz0), "x", "stone", None, height=4.0, thick=OUTER,
           holes=[(2.0, 3.8, 1.0, 3.0), (7.3, 8.7, 0.0, DOOR_H, {"name": "hall door", "into": "+", "open": True, "tint": (0.3, 0.22, 0.18)}), (12.2, 14.0, 1.0, 3.0)])
    m.wall((hx0, hz1), (hx1, hz1), "x", "stone", None, height=4.0, thick=OUTER,
           holes=[(1.0, 2.4, 0.0, DOOR_H, {"name": "vestry door", "into": "+", "away": False})])
    m.wall((hx0, hz0), (hx0, hz1), "z", "stone", None, height=4.0, thick=OUTER, holes=[(3.0, 5.0, 1.0, 3.0)])
    m.wall((hx1, hz0), (hx1, hz1), "z", "stone", None, height=4.0, thick=OUTER, holes=[(3.0, 5.0, 1.0, 3.0)])
    # The stage is hollow: a crawl space under its boards, behind a panel of its front that
    # slides. Steps up either end.
    stage = (0.55, 0.4, 0.3)
    SH = 1.2
    m.span(-6.0, 6.0, SH - 0.1, SH, 32.6, hz1 - 0.15, "wood", stage)
    m.wall((-6.0, 32.7), (6.0, 32.7), "x", "wood", stage, height=SH - 0.1,
           holes=[(5.0, 6.2, 0.0, 1.05, {"name": "loose board", "secret": True, "hinge": "start"})])
    m.span(-6.0, -5.8, 0.0, SH - 0.1, 32.8, hz1 - 0.15, "wood", stage)
    m.span(5.8, 6.0, 0.0, SH - 0.1, 32.8, hz1 - 0.15, "wood", stage)
    flight(m, 6.0, 7.4, 33.2, 35.4, 0.0, SH, "w", "wood", stage, thick=0.2)
    flight(m, -7.4, -6.0, 33.2, 35.4, 0.0, SH, "e", "wood", stage, thick=0.2)
    for row in range(4):
        for bx in (-4.5, -2.5, 2.5, 4.5):
            m.prop("furniture_bench", bx, 28.0 + row * 1.1, 180.0)
    m.prop("furniture_speaker", -5.4, 34.5, y=1.2)
    m.prop("furniture_speaker", 5.4, 34.5, y=1.2)
    m.prop("furniture_desk", 0.0, 34.0, 180.0, y=1.2)
    m.prop("furniture_cardboard_box_closed", 3.6, 35.0, 20.0)

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
    m.floor_around(-46, -42, 46, 42, (10.0, 18.0, 30.0, 32.0), "grass", y=GROUND)
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
    m.wall((tx0, tz0), (tx1, tz0), "x", "brick", None, height=2.8, thick=OUTER,
           holes=[(1.0, 2.4, 0.0, DOOR_H, {"name": "toilet door", "into": "+", "tint": (0.3, 0.45, 0.6)}),
                  (7.6, 9.0, 0.0, DOOR_H, {"name": "toilet door", "into": "+", "hinge": "end", "tint": (0.6, 0.3, 0.4)})])
    m.wall((tx0, tz1), (tx1, tz1), "x", "brick", None, height=2.8, thick=OUTER)
    m.wall((tx0, tz0), (tx0, tz1), "z", "brick", None, height=2.8, thick=OUTER)
    m.wall((tx1, tz0), (tx1, tz1), "z", "brick", None, height=2.8, thick=OUTER)
    # Between the two sides, by the doors, a panel of tiles that slides: the one way from one
    # to the other that is not outside.
    m.wall((35.0, tz0), (35.0, tz1), "z", "tile", None, height=2.8,
           holes=[(0.4, 1.4, 0.0, 2.0, {"name": "loose tiles", "secret": True, "hinge": "end"})])
    bathroom(m, tx0 + 0.15, tz0 + 2.0, 34.9, tz1 - 0.15, stalls=3)
    bathroom(m, 35.1, tz0 + 2.0, tx1 - 0.15, tz1 - 0.15, stalls=3)

    # The tennis court, south-west, fenced, with a gate.
    cx0, cx1, cz0, cz1 = -40.0, -20.0, -36.0, -18.0
    m.overlay(cx0, cz0, cx1, cz1, "asphalt", (0.35, 0.55, 0.4))
    for (a, b, c, d) in ((cx0 + 1, cz0 + 1, cx1 - 1, cz0 + 1.1), (cx0 + 1, cz1 - 1.1, cx1 - 1, cz1 - 1), (cx0 + 1, cz0 + 1, cx0 + 1.1, cz1 - 1),
                         (cx1 - 1.1, cz0 + 1, cx1 - 1, cz1 - 1), (-30.05, cz0 + 1, -29.95, cz1 - 1)):
        m.overlay(a, b, c, d, "line")
    m.span(-30.05, -29.95, 0.0, 1.0, cz0 + 0.5, cz1 - 0.5, "fabric", (0.15, 0.15, 0.15))
    fence(m, cx0, cz0, cx1, cz1, height=3.0, mat="metal", tint=(0.4, 0.42, 0.45), gaps=[("e", 7.0, 9.0)],
          gate={"name": "court gate", "into": "+", "leaves": 2, "open": True})
    # Clear of the gate: by it, the bench left no room round the open leaf's free edge, and a
    # stand-in stood in the corner between leaf, fence and bench (game-hide-n-seek's
    # headless_bots, eighteen seconds).
    m.prop("furniture_bench", -18.6, -33.0, -90.0)
    m.prop("furniture_cardboard_box_open", -18.6, -35.0, 10.0)

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
    # The front door opens in: out, it would scrape the porch.
    m.wall((rx0, rz0), (rx1, rz0), "x", "wood", logs, height=2.8, thick=OUTER,
           holes=[(4.3, 5.7, 0.0, DOOR_H, {"name": "cabin door", "into": "+", "away": False}), (1.0, 2.6, SILL, HEAD), (7.4, 9.0, SILL, HEAD)])
    m.wall((rx0, rz1), (rx1, rz1), "x", "wood", logs, height=2.8, thick=OUTER,
           holes=[(2.0, 3.4, 0.0, DOOR_H, {"name": "back door", "into": "-"})])
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

    # The woods: a ring of trees, rocks, logs and bushes between the paths and the hedge; three of
    # the trees are made to be climbed (game-hide-n-seek's tools/climb_trees.py).
    kinds = ["nature_tree_pine_tall_a", "nature_tree_default", "nature_tree_oak", "nature_tree_cone", "nature_tree_fat", "nature_tree_detailed"]
    tree_ring(m, -42.0, -38.0, 42.0, 38.0, 7.0, kinds, seed=5)
    undergrowth = [(-38.0, -6.5, "nature_rock_large_a"), (-37.5, 7.5, "nature_log_large"), (-36.0, -12.0, "nature_plant_bush_detailed"),
                   (-34.0, 4.0, "nature_stump_old"), (36.0, -14.0, "nature_rock_tall_a"), (40.0, -4.0, "nature_log_large"),
                   (38.0, 28.0, "nature_plant_bush_detailed"), (34.0, 36.0, "nature_stone_large_a"), (-6.0, 37.0, "nature_log_large"),
                   (3.0, -33.0, "nature_rock_large_a"), (-10.0, -34.0, "nature_plant_bush_detailed"), (-24.0, 34.0, "nature_plant_bush_large"),
                   (24.0, -36.0, "nature_stump_round"), (-16.0, 26.0, "nature_climbtree_oak"), (-6.0, 24.0, "nature_tree_default_fall"),
                   (38.0, -30.0, "nature_tree_plateau"), (-22.0, -8.0, "nature_climbtree_oak"), (-20.0, 6.0, "nature_tree_small"),
                   (-26.0, 26.0, "nature_rock_small_a"), (2.0, 26.0, "nature_plant_bush_large"), (28.0, 10.0, "nature_climbtree_fork")]
    for (ux, uz, kind) in undergrowth:
        m.prop(kind, ux, uz, (ux * 17 + uz) % 120)

    for (x, z) in ((-6.0, 1.0), (-2.0, 1.0), (3.0, 4.0), (-8.0, 2.0), (-11.0, -11.0), (6.0, -8.0), (11.5, -1.0), (-4.0, -10.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((19.5, -7.2), (22.5, -7.2), (25.5, -7.2), (28.5, -7.2)):
        m.spawn("seekers", x, z)
    return m.build()


def slab(m, x0, z0, x1, z1, y, holes, mat, tint=None, thick=0.2):
    """A floor slab from x0..x1, z0..z1 with its top at y, leaving rectangular `holes` (hx0, hz0,
    hx1, hz1) open: an upper storey over a stairwell or two. Cut into strips at every hole's x
    edges, and each strip into the z runs no hole covers, so it is a handful of boxes."""
    xs = sorted({x0, x1} | {h[0] for h in holes if x0 < h[0] < x1} | {h[2] for h in holes if x0 < h[2] < x1})
    for a, b in zip(xs, xs[1:]):
        cuts = sorted((max(h[1], z0), min(h[3], z1)) for h in holes if h[0] < b and h[2] > a)
        at = z0
        for c0, c1 in cuts:
            if c0 > at:
                m.span(a, b, y - thick, y, at, c0, mat, tint)
            at = max(at, c1)
        if at < z1:
            m.span(a, b, y - thick, y, at, z1, mat, tint)


def lights_over(m, x0, z0, x1, z1, height, energy=1.0, colour=(1.0, 0.95, 0.85)):
    """A room's ceiling lamps and their lights, one per eight metres or so, under `height`."""
    cols = max(1, int(round((x1 - x0) / 8.0)))
    rows = max(1, int(round((z1 - z0) / 8.0)))
    for i in range(cols):
        for j in range(rows):
            lx = x0 + (i + 0.5) * (x1 - x0) / cols
            lz = z0 + (j + 0.5) * (z1 - z0) / rows
            m.lamp_panel(lx, lz, height)
            m.light(lx, height - 0.5, lz, rng=max(x1 - x0, z1 - z0) / max(cols, rows) * 1.25 + 3.0, energy=energy, colour=colour)


def classroom(m, x0, z0, x1, z1, y, corridor):
    """A classroom: a whiteboard on the west wall, the teacher's desk in front of it, three rows
    of three desks facing it, a bookcase and boxes. `corridor` is the side its door is on ('n' or
    's'), and the rows keep a door's swing clear of that side."""
    zc = (z0 + z1) / 2
    m.span(x0 + 0.1, x0 + 0.14, y + 1.0, y + 2.2, zc - 1.8, zc + 1.8, "whiteboard")
    away = -1 if corridor == "s" else 1
    m.prop("furniture_desk", x0 + 1.5, zc + away * 2.6, 90.0, y=y)
    m.prop("furniture_chair_desk", x0 + 0.8, zc + away * 2.6, 90.0, y=y)
    for i, cx in enumerate((x0 + 3.2, x0 + 5.0, x0 + 6.8)):
        if cx + 0.9 > x1:
            continue
        for k, dz in enumerate((3.2, 0.4, -2.4)):
            z = zc + away * dz
            m.prop("furniture_desk", cx, z, -90.0, y=y)
            m.prop("furniture_chair_desk", cx + 0.85, z, -90.0, y=y)
    m.against("furniture_bookcase_closed", "e", x1 - 0.1, zc + away * 3.6, y=y)
    m.prop("furniture_cardboard_box_closed", x1 - 0.5, zc - away * 0.6, 15.0, y=y)


def school():
    """Brookside School: two storeys of classrooms off one long corridor with a stairwell at
    each end, a cafeteria, a library with a room nobody is meant to find, a gym as tall as the
    building with bleachers and a balcony, and a playground and a field outside.

    The two stairwells are the way round: a hider seen on one floor goes up one and down the
    other. The gym is the way up and the way under at once, its balcony reached from the top
    corridor or by a stair from the floor, its bleachers hollow underneath. And outside, the
    portable classroom stands on blocks with a crawl space under it."""
    m = Map(
        "hs_school", "Brookside School",
        "Two floors of classrooms, two stairwells, a gym with bleachers and a balcony, a library with a secret.",
        {"sky_top": [0.34, 0.54, 0.84], "sky_horizon": [0.78, 0.84, 0.9], "ground": [0.3, 0.33, 0.27],
         "sun_pitch": -50, "sun_yaw": 160, "sun_energy": 1.0, "sun_colour": [1.0, 0.96, 0.9],
         "ambient": [0.62, 0.64, 0.7], "ambient_energy": 0.5, "fog": 0.0015},
        kill_y=-10.0,
    )
    X0, X1, Z0, Z1 = -30.0, 12.0, -12.0, 12.0     # the classroom block; the gym is east of it to x 30
    GX = 30.0
    H = 3.6
    Y = (0.0, H)
    TOP = 2 * H
    cream = (0.93, 0.9, 0.82)
    blue = (0.3, 0.45, 0.68)
    door = {"into": "+", "tint": blue}

    # The grounds: grass, the playground's asphalt to the north, the field to the south.
    m.floor(-50, -45, 50, 45, "grass", y=GROUND)
    m.overlay(-34, -42, 16, -14, "asphalt")
    for x in frange(-30, 12, 6.0):
        m.overlay(x, -30.1, x + 3.0, -29.9, "line", (1.0, 0.95, 0.6))
    m.overlay(-36, 14, 34, 16, "sidewalk")
    m.overlay(-40, -1.5, -30, 1.5, "sidewalk")
    boundary(m, -50, -45, 50, 45, height=2.6, mat="metal", tint=(0.35, 0.4, 0.35))

    # --- The classroom block ------------------------------------------------
    m.floor(X0, Z0, X1, Z1, "tile_check", (0.9, 0.88, 0.84))
    stairs = [(-12.0, -8.0), (8.0, 12.0)]
    slab(m, X0, Z0, X1, Z1, H, [(a, Z0, b, -2.0) for a, b in stairs], "tile_small", (0.86, 0.86, 0.84))
    m.ceiling(X0, Z0, X1, Z1, TOP, "ceiling", None, "roof", (0.4, 0.3, 0.28), 0.4)

    for k, y in enumerate(Y):
        # Outer walls, brick with windows; the front doors at the west end of the corridor, a
        # back door from the cafeteria to the playground.
        north = [(2.0, 3.8, SILL, HEAD), (12.0, 13.8, SILL, HEAD), (24.0, 25.8, SILL, HEAD), (32.0, 33.8, SILL, HEAD)]
        south = [(2.0, 3.8, SILL, HEAD), (13.0, 14.8, SILL, HEAD), (20.0, 21.8, SILL, HEAD), (28.0, 29.8, SILL, HEAD), (36.0, 37.8, SILL, HEAD)]
        west = [(3.0, 4.8, SILL, HEAD), (19.0, 20.8, SILL, HEAD)]
        if k == 0:
            north[0] = (6.0, 7.4, 0.0, DOOR_H, {"name": "playground door", "into": "+", "away": False, "mat": "metal", "tint": blue})
            west.append((10.6, 13.4, 0.0, DOOR_H, {"name": "front door", "into": "+", "away": False, "angle": 88.0, "leaves": 2, "open": True, "tint": blue}))
        else:
            west.append((11.2, 12.8, SILL, HEAD))
        m.wall((X0, Z0), (X1, Z0), "x", "brick", None, height=H, y0=y, thick=OUTER, holes=north, inner="paint", inner_tint=cream)
        m.wall((X0, Z1), (X1, Z1), "x", "brick", None, height=H, y0=y, thick=OUTER, holes=south)
        m.wall((X0, Z0), (X0, Z1), "z", "brick", None, height=H, y0=y, thick=OUTER, holes=west)

        # The corridor between z -1.5 and 1.5. The stairwells open onto it with no door.
        big = {"into": "-", "away": False, "leaves": 2, "tint": blue}
        m.wall((X0, -1.5), (X1, -1.5), "x", "paint", cream, height=H, y0=y,
               holes=[(7.0, 9.8, 0.0, DOOR_H, dict(big, name="library doors" if k else "cafeteria doors")),
                      (18.0, 22.0, 0.0, H),
                      (27.8, 29.2, 0.0, DOOR_H, dict(door, name="classroom door", into="-")),
                      (35.8, 37.2, 0.0, DOOR_H, dict(door, name="lab door" if k else "classroom door", into="-")),
                      (38.0, 42.0, 0.0, H)])
        m.wall((X0, 1.5), (X1, 1.5), "x", "paint", cream, height=H, y0=y,
               holes=[(3.0, 4.4, 0.0, DOOR_H, dict(door, name="art room door" if k else "kitchen door")),
                      (10.0, 11.4, 0.0, DOOR_H, dict(door, name="toilet door")),
                      (14.5, 15.9, 0.0, DOOR_H, dict(door, name="toilet door")),
                      (23.8, 25.2, 0.0, DOOR_H, dict(door, name="classroom door")),
                      (30.0, 31.4, 0.0, DOOR_H, dict(door, name="music room door" if k else "staff room door")),
                      (39.8, 41.2, 0.0, DOOR_H, dict(door, name="classroom door"))])
        for x in (-12.0, -8.0, 0.0, 8.0):
            m.wall((x, Z0), (x, -1.5), "z", "paint", cream, height=H, y0=y)
        for x in (-21.0, -16.5, -12.0, -4.0, 4.0):
            m.wall((x, 1.5), (x, Z1), "z", "paint", cream, height=H, y0=y)

        # Lockers along the corridor, kept clear of every door's swing.
        for a, b in ((-28.0, -23.6), (-19.6, -12.4), (-7.6, -2.9), (0.4, 5.1)):
            lockers(m, a, b, -1.4, "n", y=y)
        for a, b in ((-25.0, -20.6), (-14.0, -7.0), (-3.4, -0.6), (2.0, 9.2)):
            lockers(m, a, b, 1.4, "s", y=y)
        lights_over(m, X0, -1.5, X1, 1.5, y + H - 0.2, energy=0.9)
        for (a, b, c, d) in ((-30, -12, -12, -1.5), (-8, -12, 0, -1.5), (0, -12, 8, -1.5), (-30, 1.5, -21, 12),
                             (-21, 1.5, -12, 12), (-12, 1.5, -4, 12), (-4, 1.5, 4, 12), (4, 1.5, 12, 12)):
            lights_over(m, a, b, c, d, y + H - 0.2)

        # Classrooms, both floors: two to the north, two to the south.
        classroom(m, -7.9, Z0 + 0.15, -0.1, -1.6, y, "s")
        classroom(m, 4.1, 1.6, 11.9, Z1 - 0.15, y, "n")
        classroom(m, -11.9, 1.6, -4.1, Z1 - 0.15, y, "n")
        if k == 0:
            classroom(m, 0.1, Z0 + 0.15, 7.9, -1.6, y, "s")
        # Toilets, both floors, a stall door on every stall.
        bathroom(m, -20.85, 3.6, -16.6, Z1 - 0.15, stalls=2, y=y)
        bathroom(m, -16.4, 3.6, -12.15, Z1 - 0.15, stalls=2, y=y)

    # The stairwells: a switchback in each, its landing over a place to crouch on the ground floor.
    for sx, _ in stairs:
        for y in (0.0,):
            flight(m, sx, sx + 2.0, -6.5, -2.0, y, y + H / 2, "n", "concrete", (0.75, 0.75, 0.74))
            m.span(sx, sx + 4.0, y + H / 2 - 0.2, y + H / 2, Z0 + 0.15, -6.5, "concrete", (0.75, 0.75, 0.74))
            flight(m, sx + 2.0, sx + 4.0, -6.5, -2.0, y + H / 2, y + H, "s", "concrete", (0.75, 0.75, 0.74))
            railing(m, sx + 2.0, -6.5, sx + 2.0, -2.0, y + H / 2, height=0.9)
        railing(m, sx, -2.0, sx + 2.0, -2.0, H)
        m.light(sx + 2.0, H + 2.6, -7.0, rng=8.0, energy=0.8)

    # Ground floor: the cafeteria and its kitchen, the staff room.
    # A metre back from the corridor: the cafeteria doors open into the room, and the nearest
    # bench stood just past a leaf's free edge, so nobody could get round an open leaf
    # (game-hide-n-seek's headless_bots, a stand-in there fifteen seconds).
    for row in range(3):
        for col in range(2):
            t = m.prop("furniture_table", -26.0 + col * 7.0, -10.0 + row * 2.6, 0.0)
            m.prop("furniture_bench", -26.5 + col * 7.0, -10.75 + row * 2.6, 0.0)
            m.prop("furniture_bench", -25.5 + col * 7.0, -9.25 + row * 2.6, 180.0)
    for i in range(5):
        m.prop("furniture_kitchen_bar", -17.2 + i * 1.0, -3.0, 180.0)
    m.prop("furniture_trashcan", -29.2, -2.2)
    kitchen_row(m, -29.85, -24.0, Z1 - 0.15, "s")
    m.prop("furniture_kitchen_fridge_large", -22.0, Z1 - 0.65, 180.0)
    m.prop("furniture_kitchen_bar", -27.0, 6.0, 0.0)
    m.prop("furniture_kitchen_bar", -26.0, 6.0, 0.0)
    m.prop("furniture_kitchen_bar_end", -25.3, 6.0, 0.0)
    m.prop("furniture_cardboard_box_closed", -22.0, 3.2, 20.0)
    m.prop("furniture_cardboard_box_open", -22.6, 3.6, -10.0)
    m.prop("furniture_lounge_sofa", -1.0, Z1 - 0.7, 180.0)
    m.prop("furniture_table_coffee", -1.0, 9.6)
    m.prop("furniture_lounge_chair", 2.6, 9.0, -90.0)
    m.against("furniture_kitchen_cabinet", "w", -3.9, 5.0)
    m.against("furniture_kitchen_fridge", "w", -3.9, 6.0)
    m.against("furniture_bookcase_closed_doors", "e", 3.9, 4.0)
    m.prop("furniture_table_round", 1.6, 5.8)

    # Upstairs: the library (and its archive), the science lab, the art room, the music room.
    LY = H
    for i, bz in enumerate((-10.4, -7.6, -4.8)):
        for bx in (-22.0, -19.0, -16.0):
            m.prop("furniture_bookcase_closed_wide" if (i + int(bx)) % 2 else "furniture_bookcase_open", bx, bz, 0.0, y=LY)
    m.prop("furniture_table", -26.6, -3.8, 0.0, y=LY)
    m.prop("furniture_chair", -27.2, -3.0, 180.0, y=LY)
    m.prop("furniture_chair", -26.0, -3.0, 180.0, y=LY)
    m.prop("furniture_desk", -14.0, -3.0, 0.0, y=LY)
    # The archive: a small room in the library's north-west corner whose only way in is a
    # panel of the library's wall that slides, between two bookcases.
    m.wall((-26.5, Z0), (-26.5, -6.0), "z", "paint", cream, height=H, y0=LY,
           holes=[(2.0, 3.0, 0.0, 2.1, {"name": "panel", "secret": True, "hinge": "start"})])
    m.wall((X0, -6.0), (-26.5, -6.0), "x", "paint", cream, height=H, y0=LY)
    m.against("furniture_bookcase_closed", "w", -26.4, -11.2, y=LY)
    m.against("furniture_bookcase_closed", "w", -26.4, -8.4, y=LY)
    m.prop("furniture_cardboard_box_closed", -29.0, -11.0, 10.0, y=LY)
    m.prop("furniture_cardboard_box_open", -28.0, -7.2, -20.0, y=LY)
    m.prop("furniture_lamp_round_floor", -29.3, -6.6, y=LY)
    # The lab: benches in rows.
    for bz in (-10.0, -7.4, -4.8):
        for bx in (2.0, 5.6):
            m.prop("furniture_kitchen_bar", bx - 0.5, bz, 0.0, y=LY)
            m.prop("furniture_kitchen_bar", bx + 0.5, bz, 0.0, y=LY)
            m.prop("furniture_stool_bar", bx, bz + 0.8, 0.0, y=LY)
    m.span(0.1, 0.14, LY + 1.0, LY + 2.2, -8.5, -5.0, "whiteboard")
    # Art: tables, a sink, boxes of everything.
    for (tx, tz) in ((-27.0, 5.0), (-27.0, 8.5), (-23.5, 5.0), (-23.5, 8.5)):
        m.prop("furniture_table_cross", tx, tz, 0.0, y=LY)
    m.against("furniture_kitchen_sink", "s", Z1 - 0.15, -29.0, y=LY)
    m.against("furniture_bookcase_open", "s", Z1 - 0.15, -22.5, y=LY)
    m.prop("furniture_cardboard_box_closed", -21.6, 3.0, 5.0, y=LY)
    # Music: a stage of risers, speakers, chairs in a half ring.
    m.span(-3.8, 3.8, LY, LY + 0.4, 9.6, Z1 - 0.15, "wood", (0.6, 0.45, 0.32))
    m.span(-3.8, 3.8, LY + 0.4, LY + 0.8, 10.8, Z1 - 0.15, "wood", (0.6, 0.45, 0.32))
    m.prop("furniture_speaker", -3.4, 3.0, 0.0, y=LY)
    m.prop("furniture_speaker", 3.4, 3.0, 0.0, y=LY)
    for i, a in enumerate(range(-60, 61, 30)):
        m.prop("furniture_chair", math.sin(math.radians(a)) * 3.0, 6.0 + math.cos(math.radians(a)) * 1.2, 180.0 + a, y=LY)
    m.prop("furniture_side_table", 2.6, 9.0, 0.0, y=LY)

    # --- The gym ------------------------------------------------------------
    m.floor(X1, Z0, GX, Z1, "gym")
    m.ceiling(X1, Z0, GX, Z1, TOP, "metal", (0.75, 0.76, 0.78), "roof", (0.4, 0.3, 0.28), 0.4)
    gym_door = {"name": "gym doors", "into": "+", "away": False, "angle": 88.0, "leaves": 2, "tint": blue}
    m.wall((X1, Z0), (X1, Z1), "z", "paint", cream, height=H, y0=0.0, holes=[(10.6, 13.4, 0.0, DOOR_H, gym_door)])
    m.wall((X1, Z0), (X1, Z1), "z", "paint", cream, height=H, y0=H,
           # Hung from its north end: from the south one, opened from the balcony, it lay across
           # the top of the stair beside it with 0.7 m left by the rail (game-hide-n-seek's
           # headless_bots, a stand-in held there eleven seconds).
           holes=[(10.6, 12.0, 0.0, DOOR_H, {"name": "balcony door", "into": "+", "hinge": "end", "tint": blue})])
    m.wall((X1, Z0), (GX, Z0), "x", "brick", None, height=TOP, thick=OUTER, holes=[(4.0, 7.0, 4.6, 6.6), (10.0, 13.0, 4.6, 6.6)])
    m.wall((X1, Z1), (GX, Z1), "x", "brick", None, height=TOP, thick=OUTER,
           holes=[(8.0, 9.4, 0.0, DOOR_H, {"name": "fire door", "into": "+", "away": False, "mat": "metal", "tint": (0.5, 0.15, 0.12)}),
                  (12.0, 15.0, 4.6, 6.6)])
    m.wall((GX, Z0), (GX, Z1), "z", "brick", None, height=TOP, thick=OUTER, holes=[(5.0, 8.0, 4.6, 6.6), (16.0, 19.0, 4.6, 6.6)])
    for gx, gz in ((21.0, -6.0), (21.0, 4.0)):
        m.lamp_panel(gx, gz, TOP, w=2.0, d=0.6)
        m.light(gx, TOP - 0.8, gz, rng=16.0, energy=1.4)
    m.overlay(14.0, -10.0, 29.0, -9.9, "line", (0.9, 0.3, 0.2))
    m.overlay(14.0, 9.9, 29.0, 10.0, "line", (0.9, 0.3, 0.2))
    m.overlay(21.45, -10.0, 21.55, 10.0, "line", (0.9, 0.3, 0.2))
    # The balcony along the west wall, from the top corridor's door or up the stair in the
    # south-west corner, over the gym floor.
    m.span(X1, X1 + 3.0, H - 0.2, H, Z0 + 0.15, Z1 - 0.15, "wood", (0.6, 0.45, 0.32))
    railing(m, X1 + 3.0, Z0 + 0.15, X1 + 3.0, 4.0, H, mat="metal")
    flight(m, X1 + 3.0, X1 + 5.0, 4.0, Z1 - 0.3, 0.0, H, "s", "wood", (0.6, 0.45, 0.32), thick=0.2)
    m.prop("furniture_bench", X1 + 1.5, -8.0, 90.0, y=H)
    m.prop("furniture_cardboard_box_closed", X1 + 0.6, 2.0, 0.0, y=H)
    # Bleachers along the south wall, hollow underneath.
    flight(m, 19.0, GX - 0.15, 6.0, Z1 - 0.15, 0.0, 2.6, "s", "wood", (0.66, 0.5, 0.34), thick=0.12)
    # The storeroom in the north-east corner, behind a shutter: mats and boxes.
    m.wall((26.0, Z0), (26.0, -7.0), "z", "paint", cream, height=3.0,
           holes=[(1.0, 4.0, 0.0, 2.6, {"name": "storeroom shutter", "motion": "lift", "travel": 2.4, "mat": "metal", "tint": (0.55, 0.57, 0.6), "seconds": 1.2})])
    m.wall((26.0, -7.0), (GX, -7.0), "x", "paint", cream, height=3.0)
    m.span(26.0, GX - 0.15, 3.0, 3.1, Z0 + 0.15, -7.0, "metal", (0.6, 0.6, 0.62))
    m.span(27.0, 29.6, 0.0, 0.5, -11.6, -9.4, "fabric", (0.2, 0.35, 0.7))
    m.span(27.0, 29.6, 0.5, 1.0, -11.6, -9.4, "fabric", (0.2, 0.35, 0.7))
    m.prop("furniture_cardboard_box_closed", 27.4, -8.0, 10.0)
    m.prop("furniture_cardboard_box_closed", 28.4, -7.8, -20.0)
    # On the floor: crash mats to get behind, benches.
    m.span(14.0, 16.4, 0.0, 0.6, -9.0, -6.0, "fabric", (0.2, 0.35, 0.7))
    m.span(23.0, 25.0, 0.0, 0.9, -2.0, 0.6, "fabric", (0.2, 0.35, 0.7))
    m.prop("furniture_bench", 18.0, -11.2, 0.0)
    m.prop("furniture_bench", 19.2, -11.2, 0.0)

    # --- Outside ------------------------------------------------------------
    # The portable classroom, on blocks: a crawl space 1.1 m high under its whole floor.
    px0, px1, pz0, pz1 = -12.0, 0.0, -34.0, -26.0
    PY = CRAWL_H + 0.1
    for bx in frange(px0 + 0.3, px1, 2.85):
        for bz in (pz0 + 0.3, pz1 - 0.3):
            m.span(bx - 0.2, bx + 0.2, 0.0, PY - 0.1, bz - 0.2, bz + 0.2, "concrete")
    room_at(m, px0, pz0, px1, pz1, PY, "carpet", (0.5, 0.55, 0.6), height=2.7, roof_tint=(0.3, 0.3, 0.32))
    portable = (0.85, 0.85, 0.78)
    m.wall((px0, pz0), (px1, pz0), "x", "wood", portable, height=2.7, y0=PY, thick=0.15, holes=[(2.0, 3.6, 0.9, 2.0), (8.0, 9.6, 0.9, 2.0)])
    m.wall((px0, pz1), (px1, pz1), "x", "wood", portable, height=2.7, y0=PY, thick=0.15,
           holes=[(5.3, 6.7, 0.0, 2.1, {"name": "portable's door", "into": "-", "away": False, "tint": blue}), (1.5, 3.1, 0.9, 2.0)])
    m.wall((px0, pz0), (px0, pz1), "z", "wood", portable, height=2.7, y0=PY, thick=0.15)
    m.wall((px1, pz0), (px1, pz1), "z", "wood", portable, height=2.7, y0=PY, thick=0.15)
    flight(m, -6.9, -5.1, pz1, pz1 + 2.0, 0.0, PY, "n", "wood", (0.55, 0.42, 0.3), thick=0.2)
    for i, cx in enumerate((-9.5, -7.0, -4.5, -2.0)):
        m.prop("furniture_desk", cx, -31.5, 0.0, y=PY)
        m.prop("furniture_chair_desk", cx, -30.7, 180.0, y=PY)
    m.span(px0 + 0.1, px0 + 0.14, PY + 1.0, PY + 2.0, -32.0, -28.0, "whiteboard")
    m.prop("furniture_cardboard_box_closed", -0.6, -33.3, 0.0, y=PY)
    # The bike shed, benches, a few trees round the playground.
    m.span(16.0, 28.0, 2.4, 2.5, -32.0, -28.5, "roof")
    for bx in (16.1, 22.0, 27.9):
        m.span(bx - 0.08, bx + 0.08, 0.0, 2.4, -28.6, -28.4, "metal")
    m.span(16.0, 28.0, 0.0, 2.4, -32.1, -31.9, "metal", (0.4, 0.42, 0.45))
    for i in range(6):
        m.span(17.0 + i * 1.8, 17.08 + i * 1.8, 0.0, 0.8, -31.6, -29.4, "metal", (0.2, 0.2, 0.22))
    for (bx, bz, yaw) in ((-20.0, -16.0, 0.0), (-15.0, -16.0, 0.0), (6.0, -16.0, 0.0), (-24.0, -38.0, 180.0)):
        m.prop("furniture_bench", bx, bz, yaw)
    for (tx, tz, kind) in ((-40.0, -38.0, "nature_tree_oak"), (-42.0, -20.0, "nature_tree_default"), (24.0, -40.0, "nature_tree_fat"),
                           (40.0, -30.0, "nature_tree_oak_fall"), (42.0, -10.0, "nature_tree_tall"), (40.0, 30.0, "nature_tree_default"),
                           (-42.0, 30.0, "nature_tree_detailed"), (-40.0, 10.0, "nature_tree_pine_round_a")):
        m.prop(kind, tx, tz, (tx * 7) % 90)
    for (bx, bz) in ((-36.0, -26.0), (-36.0, -8.0), (36.0, -20.0), (36.0, 8.0), (-34.0, 22.0), (34.0, 38.0), (14.0, -18.0)):
        m.prop("nature_plant_bush_large", bx, bz)
    # The field: two goals and a line round it.
    for gz in (19.0, 41.0):
        side = 1 if gz < 30 else -1
        m.span(-3.0, -2.9, 0.0, 2.2, gz - 0.05, gz + 0.05, "metal", (0.95, 0.95, 0.95))
        m.span(2.9, 3.0, 0.0, 2.2, gz - 0.05, gz + 0.05, "metal", (0.95, 0.95, 0.95))
        m.span(-3.0, 3.0, 2.2, 2.3, gz - 0.05, gz + 0.05, "metal", (0.95, 0.95, 0.95))
        m.span(-3.0, 3.0, 0.0, 2.3, gz - side * 1.2 - 0.03, gz - side * 1.2 + 0.03, "fabric", (0.9, 0.9, 0.9))
    for (a, b, c, d) in ((-20, 18, 20, 18.1), (-20, 42, 20, 42.1), (-20, 18, -19.9, 42), (19.9, 18, 20, 42), (-20, 29.95, 20, 30.05)):
        m.overlay(a, b, c, d, "line")
    m.prop("furniture_bench", -22.0, 26.0, 90.0)
    m.prop("furniture_bench", -22.0, 34.0, 90.0)

    for (x, z) in ((-24.0, 0.0), (-18.0, 0.0), (-12.0, 0.0), (-6.0, 0.0), (0.0, 0.0), (6.0, 0.0), (-10.0, -0.4), (2.0, 0.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((-8.0, 24.0), (-4.0, 24.0), (4.0, 24.0), (8.0, 24.0)):
        m.spawn("seekers", x, z)
    return m.build()


def fitting_rooms(m, x0, z_back, s, y, booths=2):
    """Changing rooms along a shop's back wall: booths 1.3 m wide and 1.4 deep, a curtain-door
    on each that swings out. `s` is the way from the shop's front to its back (-1 or 1)."""
    w, depth = 1.3, 1.4
    z_front = z_back - s * depth
    for i in range(booths + 1):
        px = x0 + i * w
        m.span(px - 0.03, px + 0.03, y, y + 2.1, min(z_front, z_back), max(z_front, z_back), "wood", (0.85, 0.82, 0.78))
    for i in range(booths):
        m.door("x", x0 + i * w + 0.03, x0 + (i + 1) * w - 0.03, z_front, y + 0.2, y + 2.0, name="changing room curtain",
               hinge="start", into="+" if s < 0 else "-", away=False, angle=90.0, mat="fabric", tint=(0.55, 0.2, 0.25))


def shop(m, a, front, s, y, kind):
    """One shop, x a..a+8, from its `front` wall back 7.5 m in direction `s`, furnished as `kind`.
    The front metre and a half is left clear for its shutter or door, and the back-left corner
    for its back door."""
    def z(depth):
        return front + s * depth
    face = 0.0 if s > 0 else 180.0
    if kind == "clothes":
        for i, dx in enumerate((2.2, 4.0, 5.8)):
            m.prop("furniture_coat_rack_standing", a + dx, z(2.6), 0.0, y=y)
            m.prop("furniture_coat_rack_standing", a + dx, z(4.2), 45.0, y=y)
        m.against("furniture_bookcase_open", "e", a + 7.9, z(2.4), y=y)
        m.against("furniture_bookcase_open", "e", a + 7.9, z(3.4), y=y)
        m.prop("furniture_kitchen_bar", a + 1.4, z(5.4), face + 90.0, y=y)
        fitting_rooms(m, a + 4.2, front + s * 7.4, s, y)
    elif kind == "tech":
        for dx in (2.0, 5.4):
            for d in (2.8, 5.0):
                t = m.prop("furniture_table", a + dx, z(d), 0.0, y=y)
                m.on("furniture_computer_screen" if d < 4 else "furniture_laptop", t, dx=-0.4)
                m.on("furniture_television_modern" if dx > 4 else "furniture_computer_screen", t, dx=0.5)
        m.against("furniture_bookcase_closed_wide", "e", a + 7.9, z(6.6), y=y)
        m.prop("furniture_cardboard_box_closed", a + 6.8, z(6.9), 10.0, y=y)
        m.prop("furniture_cardboard_box_closed", a + 6.4, z(6.5), 40.0, y=y)
    elif kind == "books":
        for d in (2.8, 4.4, 6.0):
            m.prop("furniture_bookcase_open", a + 3.0, z(d), face, y=y)
            m.prop("furniture_bookcase_closed", a + 4.0, z(d), face, y=y)
            m.prop("furniture_bookcase_open", a + 6.4, z(d), face, y=y)
        m.prop("furniture_lounge_chair", a + 1.4, z(3.0), 90.0, y=y)
    elif kind == "home":
        m.prop("furniture_lounge_sofa", a + 4.0, z(3.0), face + 180.0, y=y)
        m.prop("furniture_table_coffee", a + 4.0, z(4.3), 0.0, y=y)
        m.prop("furniture_lamp_square_floor", a + 6.8, z(2.6), y=y)
        m.prop("furniture_bed_double", a + 5.0, z(6.0), face + 180.0, y=y)
        m.against("furniture_bookcase_closed_doors", "e", a + 7.9, z(4.6), y=y)
    elif kind == "toys":
        m.prop("furniture_bear", a + 4.0, z(3.0), face + 180.0, y=y)
        for i in range(6):
            m.prop("furniture_cardboard_box_closed", a + 2.0 + (i % 3) * 1.6, z(4.6 + (i // 3) * 1.4), i * 23.0, y=y)
        m.against("furniture_bookcase_closed_wide", "e", a + 7.9, z(2.8), y=y)
        m.prop("furniture_side_table", a + 6.2, z(6.4), face, y=y)
    else:   # a cafe
        for (dx, d) in ((2.4, 2.8), (5.4, 2.8), (2.4, 5.2)):
            m.prop("furniture_table_round", a + dx, z(d), y=y)
            m.prop("furniture_chair_rounded", a + dx - 0.8, z(d), 90.0, y=y)
            m.prop("furniture_chair_rounded", a + dx + 0.8, z(d), -90.0, y=y)
        for i in range(3):
            bar = m.prop("furniture_kitchen_bar", a + 5.0 + i * 1.0, z(5.8), face, y=y)
        m.on("furniture_kitchen_coffee_machine", bar)
        m.against("furniture_kitchen_fridge", "e", a + 7.9, z(6.9), y=y)


def mall():
    """Galleria Mall: two floors of shops round a glass-roofed atrium, a food court and a
    supermarket below, a cinema and a furniture showroom above, service corridors behind the
    shops on both floors, and the car park.

    The atrium is the open middle where everybody can see everybody; the shops are the rooms
    off it, and the service corridors behind them are the way round: every shop has a back
    door, and the corridors have a stair at each end, so a hider chased into a shop can leave
    by the back and come out three shops along, or on the other floor."""
    m = Map(
        "hs_mall", "Galleria Mall",
        "Two floors of shops round an atrium, a cinema, a food court, and corridors behind it all.",
        {"sky_top": [0.3, 0.46, 0.76], "sky_horizon": [0.8, 0.82, 0.86], "ground": [0.3, 0.3, 0.3],
         "sun_pitch": -60, "sun_yaw": 30, "sun_energy": 1.0, "sun_colour": [1.0, 0.97, 0.92],
         "ambient": [0.66, 0.66, 0.7], "ambient_energy": 0.55, "fog": 0.001},
        kill_y=-10.0,
    )
    # The pilot of the photographic look (HsMaterials.PBR_KINDS); the other hand-made maps keep the pastel grid.
    m.doc["look"] = "pbr"
    X0, X1, Z0, Z1 = -36.0, 36.0, -16.0, 16.0
    H = 4.0
    Y = (0.0, H)
    TOP = 2 * H
    marble = (0.92, 0.9, 0.86)
    wallc = (0.95, 0.94, 0.9)
    service = (0.7, 0.72, 0.74)
    glass = {"mat": "glass", "tint": (1.0, 1.0, 1.0)}

    m.floor(-52, -40, 52, 48, "asphalt", (0.5, 0.5, 0.52), y=GROUND)
    boundary(m, -52, -40, 52, 48, height=2.6, mat="metal", tint=(0.4, 0.42, 0.45))

    # --- The building's shell: floors, the upper slab with the atrium and the service stairs
    # cut out of it, the roof with a skylight over the atrium.
    m.floor(X0, Z0, X1, Z1, "tile", marble)
    void = [(-24.0, -3.0, -11.0, 3.0), (-9.0, -3.0, 9.0, 3.0), (11.0, -3.0, 24.0, 3.0)]
    stairs = [(26.0, 12.6, 34.0, 14.6), (-34.0, -14.6, -26.0, -12.6)]
    slab(m, X0, Z0, X1, Z1, H, void + stairs, "tile", marble)
    slab(m, X0, Z0, X1, Z1, TOP + 0.1, [(-24.0, -5.0, 24.0, 5.0)], "ceiling", None, thick=0.1)
    slab(m, X0 - 0.4, Z0 - 0.4, X1 + 0.4, Z1 + 0.4, TOP + 0.35, [(-24.0, -5.0, 24.0, 5.0)], "roof", (0.45, 0.45, 0.48), thick=0.25)
    m.span(-24.0, 24.0, TOP + 0.1, TOP + 0.15, -5.0, 5.0, "glass")
    for x in frange(-24.0, 24.1, 4.0):
        m.span(x - 0.08, x + 0.08, TOP + 0.05, TOP + 0.35, -5.0, 5.0, "metal", (0.5, 0.52, 0.55))

    for k, y in enumerate(Y):
        # Outer walls: brick, glass doors at the two ends of the ground floor, service doors at
        # the corners, windows along the top.
        upper = [(4.0, 8.0, 1.2, 3.2), (20.0, 24.0, 1.2, 3.2), (40.0, 44.0, 1.2, 3.2), (56.0, 60.0, 1.2, 3.2)]
        main = {"name": "entrance doors", "leaves": 2, "open": True, "away": False, "into": "+", "angle": 88.0, **glass}
        m.wall((X0, Z0), (X1, Z0), "x", "brick", (0.75, 0.6, 0.55), height=H, y0=y, thick=OUTER, holes=upper if k else [])
        m.wall((X0, Z1), (X1, Z1), "x", "brick", (0.75, 0.6, 0.55), height=H, y0=y, thick=OUTER, holes=upper if k else [])
        west = [(14.0, 18.0, 0.0, 2.6, main), (1.6, 3.0, 0.0, DOOR_H, {"name": "service door", "into": "+", "away": False, "mat": "metal", "tint": service})] if k == 0 else []
        east = [(14.0, 18.0, 0.0, 2.6, dict(main, into="-")), (29.0, 30.4, 0.0, DOOR_H, {"name": "service door", "into": "-", "away": False, "mat": "metal", "tint": service})] if k == 0 else []
        m.wall((X0, Z0), (X0, Z1), "z", "brick", (0.75, 0.6, 0.55), height=H, y0=y, thick=OUTER, holes=west)
        m.wall((X1, Z0), (X1, Z1), "z", "brick", (0.75, 0.6, 0.55), height=H, y0=y, thick=OUTER, holes=east)

        # The shop fronts on the concourse: a roller shutter below (two of them down), a glass
        # door and a display window above.
        kinds = ["clothes", "tech", "cafe", "books", "home", "toys"]
        for side, front, s in (("n", -5.0, -1), ("s", 5.0, 1)):
            holes = []
            for i in range(6):
                if k == 0:
                    holes.append((8 * i + 2.0, 8 * i + 6.0, 0.0, 2.6, {"name": "shutter", "motion": "lift", "travel": 2.0, "seconds": 1.4,
                                 "mat": "metal", "tint": (0.62, 0.64, 0.68), "open": (i + (side == "s")) % 4 != 1}))
                else:
                    holes.append((8 * i + 0.5, 8 * i + 1.9, 0.0, DOOR_H, {"name": "shop door", "into": "-" if s < 0 else "+", **glass}))
                    holes.append((8 * i + 2.6, 8 * i + 6.6, 0.4, 3.0))
            m.wall((-24.0, front), (24.0, front), "x", "paint", wallc, height=H, y0=y, holes=holes)
            back = front + s * 7.5
            m.wall((-24.0, back), (24.0, back), "x", "paint", service, height=H, y0=y,
                   holes=[(8 * i + 0.6, 8 * i + 1.8, 0.0, DOOR_H, {"name": "back door", "into": "-" if s < 0 else "+", "away": False,
                           "mat": "metal", "tint": service}) for i in range(6)])
            for i in range(7):
                m.wall((-24.0 + 8 * i, min(front, back)), (-24.0 + 8 * i, max(front, back)), "z", "paint", wallc, height=H, y0=y)
            for i in range(6):
                a = -24.0 + 8 * i
                shop(m, a, front, s, y, kinds[(i + (3 if side == "s" else 0) + k * 2) % 6])
                lights_over(m, a, min(front, back), a + 8, max(front, back), y + H - 0.2, energy=1.0)
        # The ends' back walls, on the service corridors, each with a door clear of the stairs.
        sdoor = {"name": "service door", "away": False, "mat": "metal", "tint": service}
        for (xa, xb, zz, hole, into) in ((X0, -24.0, -12.5, (10.4, 11.8), "-"), (X0, -24.0, 12.5, (5.0, 6.4), "+"),
                                         (24.0, X1, -12.5, (5.0, 6.4), "-"), (24.0, X1, 12.5, (0.4, 1.8), "+")):
            m.wall((xa, zz), (xb, zz), "x", "paint", service, height=H, y0=y,
                   holes=[(hole[0], hole[1], 0.0, DOOR_H, dict(sdoor, into=into))])
        # The service corridors: bare, dim, boxes along them.
        for (za, zb) in ((Z0, -12.5), (12.5, Z1)):
            for x in frange(-30.0, 31.0, 12.0):
                m.light(x, y + H - 0.4, (za + zb) / 2, rng=9.0, energy=0.6, colour=(0.9, 0.95, 1.0))
        for (bx, bz) in ((-18.0, -15.4), (-2.0, -15.3), (14.0, -15.4), (-12.0, 15.4), (6.0, 15.3), (20.0, 15.4)):
            m.prop("furniture_cardboard_box_closed", bx, bz, bx * 7, y=y)
            m.prop("furniture_cardboard_box_closed", bx + 0.6, bz, bx * 3, y=y)

    # The service stairs, one at each end: up from the south corridor in the east, from the
    # north corridor in the west.
    flight(m, 26.0, 34.0, 12.7, 14.6, 0.0, H, "e", "concrete", (0.7, 0.7, 0.7), thick=0.25)
    railing(m, 26.0, 14.6, 34.0, 14.6, H)
    railing(m, 26.0, 12.6, 26.0, 14.6, H)
    flight(m, -34.0, -26.0, -14.6, -12.7, 0.0, H, "w", "concrete", (0.7, 0.7, 0.7), thick=0.25)
    railing(m, -34.0, -14.6, -26.0, -14.6, H)
    railing(m, -26.0, -14.6, -26.0, -12.6, H)

    # --- The atrium ---------------------------------------------------------
    # Two stairs where the escalators would be, up to the bridges, and rails round the void.
    flight(m, -19.0, -11.0, -2.0, -0.4, 0.0, H, "e", "metal", (0.6, 0.62, 0.66), thick=0.25)
    flight(m, 11.0, 19.0, 0.4, 2.0, 0.0, H, "w", "metal", (0.6, 0.62, 0.66), thick=0.25)
    for (x0, x1) in ((-24.0, -11.0), (-9.0, 9.0), (11.0, 24.0)):
        railing(m, x0, -3.0, x1, -3.0, H, height=1.1, mat="metal")
        railing(m, x0, 3.0, x1, 3.0, H, height=1.1)
    railing(m, -11.0, -3.0, -11.0, -2.0, H, height=1.1)
    railing(m, -11.0, -0.4, -11.0, 3.0, H, height=1.1)
    railing(m, -9.0, -3.0, -9.0, 3.0, H, height=1.1)
    railing(m, 9.0, -3.0, 9.0, 3.0, H, height=1.1)
    railing(m, 11.0, -3.0, 11.0, 0.4, H, height=1.1)
    railing(m, 11.0, 2.0, 11.0, 3.0, H, height=1.1)
    # The fountain under the skylight, planters, benches and kiosks on the concourse.
    m.span(-2.6, 2.6, 0.0, 0.6, -2.6, -2.2, "stone")
    m.span(-2.6, 2.6, 0.0, 0.6, 2.2, 2.6, "stone")
    m.span(-2.6, -2.2, 0.0, 0.6, -2.2, 2.2, "stone")
    m.span(2.2, 2.6, 0.0, 0.6, -2.2, 2.2, "stone")
    m.span(-2.2, 2.2, 0.4, 0.45, -2.2, 2.2, "water", None, solid=False)
    m.span(-0.4, 0.4, 0.0, 1.8, -0.4, 0.4, "stone")
    for (px, pz) in ((-22.0, -4.2), (-22.0, 4.2), (22.0, -4.2), (22.0, 4.2), (-6.0, 4.2), (6.0, -4.2)):
        m.prop("nature_pot_large", px, pz)
    for (bx, bz, yaw) in ((-6.0, -4.2, 0.0), (6.0, 4.2, 180.0), (-16.0, 4.2, 180.0), (16.0, -4.2, 0.0)):
        m.prop("furniture_bench", bx, bz, yaw)
    stall(m, 6.0, -1.0, (0.85, 0.3, 0.3))
    stall(m, -6.0, 0.0, (0.3, 0.5, 0.85))
    for x in frange(-20.0, 21.0, 8.0):
        m.light(x, H - 0.5, 0.0, rng=10.0, energy=0.9)
        m.light(x, TOP - 0.6, -4.0, rng=8.0, energy=0.7)
        m.light(x, TOP - 0.6, 4.0, rng=8.0, energy=0.7)
    for x in (-30.0, 30.0):
        m.light(x, TOP - 0.6, 0.0, rng=10.0, energy=0.5)

    # --- The food court (ground, west) and the cinema over it --------------------
    m.wall((-24.0, -12.5), (-24.0, 12.5), "z", "paint", wallc, height=H, y0=0.0, holes=[(7.5, 17.5, 0.0, H)])
    for (cz0, cz1) in ((-10.0, -3.5), (3.5, 10.0)):
        for zz in frange(cz0 + 0.5, cz1, 1.0):
            m.prop("furniture_kitchen_bar", -32.0, zz, 90.0)
        m.against("furniture_kitchen_stove", "w", X0 + 0.15, cz0 + 1.5)
        m.against("furniture_kitchen_fridge_large", "w", X0 + 0.15, cz0 + 3.5)
        m.against("furniture_kitchen_cabinet", "w", X0 + 0.15, cz0 + 5.0)
    for tx in (-29.0, -26.5):
        for tz in (-8.0, -5.0, 5.0, 8.0):
            m.prop("furniture_table_round", tx, tz)
            m.prop("furniture_chair", tx - 0.75, tz, 90.0)
            m.prop("furniture_chair", tx + 0.75, tz, -90.0)
    m.prop("furniture_trashcan", -25.0, -2.0)
    m.prop("furniture_trashcan", -25.0, 2.0)
    lights_over(m, X0, -12.5, -24.0, 12.5, H - 0.2)
    # The cinema: tiers that rise west, hollow underneath, a screen on the east wall, and at the
    # top a projection booth whose door is a panel of the back wall.
    CY = H
    m.wall((-24.0, -12.5), (-24.0, 12.5), "z", "paint", (0.3, 0.2, 0.22), height=H, y0=CY,
           holes=[(7.6, 9.0, 0.0, DOOR_H, {"name": "cinema door", "into": "-", "away": False, "tint": (0.5, 0.15, 0.18)}),
                  (16.0, 17.4, 0.0, DOOR_H, {"name": "cinema door", "into": "-", "away": False, "tint": (0.5, 0.15, 0.18)})])
    m.span(-24.3, -24.2, CY + 0.9, CY + 3.6, -3.0, 3.0, "whiteboard")
    for i in range(5):
        top = CY + 0.4 * (i + 1)
        xa, xb = -28.0 - 1.4 * (i + 1), -28.0 - 1.4 * i
        m.span(xa, xb, top - 0.12, top, -6.0, 6.0, "carpet", (0.4, 0.1, 0.12))
        for zz in (-4.6, -2.3, 0.0, 2.3, 4.6):
            m.prop("furniture_bench_cushion_low", (xa + xb) / 2 + 0.2, zz, -90.0, y=top)
    m.span(-36.0, -35.0, CY, CY + 2.0, -6.0, 6.0, "carpet", (0.4, 0.1, 0.12))
    booth = (0.3, 0.2, 0.22)
    m.span(X0, -35.0, CY + 2.0 - 0.15, CY + 2.0, -3.0, 3.0, "carpet", (0.4, 0.1, 0.12))
    m.wall((-35.0, -3.0), (-35.0, 3.0), "z", "paint", booth, height=H - 2.0, y0=CY + 2.0,
           holes=[(2.4, 3.4, 0.0, 1.85, {"name": "panel", "secret": True, "hinge": "end", "travel": 0.95}), (4.2, 5.4, 0.9, 1.4)])
    m.wall((X0, -3.0), (-35.0, -3.0), "x", "paint", booth, height=H - 2.0, y0=CY + 2.0)
    m.wall((X0, 3.0), (-35.0, 3.0), "x", "paint", booth, height=H - 2.0, y0=CY + 2.0)
    m.light(-26.0, TOP - 0.5, 0.0, rng=10.0, energy=0.35, colour=(1.0, 0.8, 0.7))

    # --- The supermarket (ground, east) and the showroom over it -----------------
    m.wall((24.0, -12.5), (24.0, 12.5), "z", "paint", wallc, height=H, y0=0.0,
           holes=[(10.5, 14.5, 0.0, 2.6, {"name": "sliding doors", "leaves": 2, "motion": "slide", "open": True, **glass})])
    for zz in (-10.4, -7.0, 7.0, 10.4):
        rack(m, 27.0, 35.0, zz, seed=int(zz * 10) + 77, depth=1.0, shelves=(1.3,), height=2.2, tint=(0.85, 0.85, 0.82))
    for zz in (-3.0, 3.0):
        m.prop("furniture_kitchen_bar", 26.4, zz, 90.0)
        m.prop("furniture_kitchen_bar_end", 26.4, zz + 1.0, 90.0)
    lights_over(m, 24.0, -12.5, X1, 12.5, H - 0.2, energy=1.1)
    m.wall((24.0, -12.5), (24.0, 12.5), "z", "paint", wallc, height=H, y0=H, holes=[(7.7, 9.3, 0.0, 3.0), (15.7, 17.3, 0.0, 3.0)])
    for i, (fx, fz) in enumerate(((28.0, -9.0), (33.0, -9.0), (28.0, -3.5), (33.0, -3.5), (28.0, 3.5), (33.0, 3.5), (28.0, 9.0), (33.0, 9.0))):
        kind = ["furniture_bed_double", "furniture_lounge_sofa_long", "furniture_lounge_design_sofa", "furniture_bed_bunk"][i % 4]
        m.prop(kind, fx, fz, (i % 2) * 180.0, y=H)
        m.prop("furniture_rug_rectangle", fx, fz + (1.6 if fz < 0 else -1.6), y=H)
    m.against("furniture_bookcase_closed_doors", "e", X1 - 0.15, -11.0, y=H)
    m.against("furniture_bookcase_closed_doors", "e", X1 - 0.15, 11.0, y=H)
    m.prop("furniture_lamp_square_floor", 25.0, -11.6, y=H)
    lights_over(m, 24.0, -12.5, X1, 12.5, TOP - 0.2, energy=1.0)

    # --- Outside: the car park to the south, the loading dock to the north -------------
    for x in frange(-44.0, 45.0, 3.0):
        m.overlay(x, 22.0, x + 0.1, 27.0, "line")
        m.overlay(x, 33.0, x + 0.1, 38.0, "line")
    tints = [(0.7, 0.12, 0.1), (0.15, 0.3, 0.6), (0.85, 0.85, 0.85), (0.2, 0.2, 0.22), (0.9, 0.75, 0.2), (0.3, 0.5, 0.35)]
    for i, x in enumerate((-41.0, -32.0, -26.0, -14.0, -5.0, 7.0, 16.0, 31.0, 40.0)):
        car(m, x + 1.5, 24.5, "z", tints[i % 6])
    for i, x in enumerate((-38.0, -20.0, -8.0, 4.0, 22.0, 34.0)):
        car(m, x + 1.5, 35.5, "z", tints[(i + 2) % 6])
    m.overlay(-46.0, 17.0, 46.0, 21.0, "sidewalk")
    container(m, -20.0, -30.0, "x", open_end="e", tint=(0.2, 0.35, 0.6))
    container(m, -6.0, -30.0, "x", open_end="w", tint=(0.6, 0.2, 0.18), doors="open")
    container(m, 10.0, -26.0, "x", tint=(0.3, 0.5, 0.35))
    m.span(-30.0, -24.0, 0.0, 1.2, -20.0, -17.0, "concrete")
    for bx in (26.0, 28.0, 30.0):
        m.span(bx, bx + 1.4, 0.0, 1.3, -22.0, -20.8, "metal", (0.2, 0.42, 0.25))
    for (tx, tz, kind) in ((-48.0, -36.0, "nature_tree_oak"), (48.0, -36.0, "nature_tree_default"), (-48.0, 44.0, "nature_tree_fat"),
                           (48.0, 44.0, "nature_tree_tall"), (-46.0, 0.0, "nature_tree_detailed"), (46.0, 0.0, "nature_tree_oak_fall"),
                           (0.0, 44.0, "nature_tree_small"), (-24.0, 44.0, "nature_tree_small"), (24.0, 44.0, "nature_tree_small")):
        m.prop(kind, tx, tz, (tx * 7) % 90)
    for (bx, bz) in ((-40.0, 18.6), (-20.0, 18.6), (20.0, 18.6), (40.0, 18.6)):
        m.prop("nature_plant_bush_large", bx, bz)

    for (x, z) in ((-20.0, 4.0), (-14.0, 4.0), (-8.0, -4.0), (8.0, 4.0), (14.0, -4.0), (20.0, -4.0), (-3.0, 4.0), (3.0, -4.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((-6.0, 29.5), (-2.0, 29.5), (2.0, 29.5), (6.0, 29.5)):
        m.spawn("seekers", x, z)
    return m.build()


def switchback(m, sx, zf, zb, y0, y1, mat="metal", tint=(0.55, 0.57, 0.6), run=2.0):
    """One storey of a switchback stair in x sx..sx+4, entered from the corridor at z `zf` and
    turning at `zb` (zb < zf): up the west half away from the corridor, round a landing at the
    back, and up the east half towards it. Stack one per storey; the upper floor wants a hole
    (sx, zb, sx + 4, zf)."""
    half = (y1 - y0) / 2
    flight(m, sx, sx + 2.0, zf - run, zf, y0, y0 + half, "n", mat, tint, thick=0.2)
    m.span(sx, sx + 4.0, y0 + half - 0.15, y0 + half, zb, zf - run, mat, tint)
    flight(m, sx + 2.0, sx + 4.0, zf - run, zf, y0 + half, y1, "s", mat, tint, thick=0.2)
    railing(m, sx + 2.0, zf - run, sx + 2.0, zf, y0 + half, height=0.9)


def lifeboat(m, x, z, y, tint=(0.95, 0.5, 0.15)):
    """An open boat 6 m by 2.2 sitting on the deck in two chocks: sides a metre high, so a
    person crouched in it is out of sight of anybody on the deck."""
    m.span(x - 3.0, x + 3.0, y + 0.3, y + 0.45, z - 1.1, z + 1.1, "wood", (0.7, 0.55, 0.4))
    m.span(x - 3.0, x + 3.0, y + 0.3, y + 1.3, z - 1.1, z - 0.98, "metal", tint)
    m.span(x - 3.0, x + 3.0, y + 0.3, y + 1.3, z + 0.98, z + 1.1, "metal", tint)
    m.span(x - 3.0, x - 2.88, y + 0.3, y + 1.3, z - 0.98, z + 0.98, "metal", tint)
    m.span(x + 2.88, x + 3.0, y + 0.3, y + 1.3, z - 0.98, z + 0.98, "metal", tint)
    for cx in (x - 1.8, x + 1.8):
        m.span(cx - 0.2, cx + 0.2, y, y + 0.3, z - 0.8, z + 0.8, "wood", (0.4, 0.3, 0.22))
    m.span(x - 0.6, x + 0.6, y + 0.45, y + 0.8, z - 0.98, z + 0.98, "wood", (0.6, 0.45, 0.32))


def ship():
    """The Marigold: a ship at her quay, four decks from the engine room to the bridge.

    A ship is the genre's best way round: two stairwells run the whole height, the promenades
    run the whole length on both sides, and the sun deck's stairs come down at both ends, so a
    chase never runs out of ship. The sea is all round it, and a hider who goes over the side is
    caught by it. The engine room is the place to crawl, among the machinery, and its after
    bulkhead has a panel that slides on the steering-gear room."""
    m = Map(
        "hs_ship", "The Marigold",
        "A ship at her quay: cabins, saloons, the engine room, lifeboats and the bridge. Don't go over the side.",
        {"sky_top": [0.3, 0.5, 0.8], "sky_horizon": [0.8, 0.86, 0.92], "ground": [0.2, 0.3, 0.38],
         "sun_pitch": -42, "sun_yaw": 200, "sun_energy": 1.1, "sun_colour": [1.0, 0.96, 0.88],
         "ambient": [0.6, 0.66, 0.74], "ambient_energy": 0.5, "fog": 0.0015},
        kill_y=-5.0,
    )
    X0, X1, BOW = -40.0, 36.0, 46.0
    B = 9.0
    E, L, M, S = -3.2, 0.0, 3.2, 6.4
    H = 3.2
    hull = (0.18, 0.22, 0.32)
    white = (0.94, 0.94, 0.92)
    teak = (0.62, 0.46, 0.32)
    inside = (0.9, 0.87, 0.8)

    def half_at(x):
        return B if x <= X1 else max(0.0, B * (1.0 - (x - X1) / (BOW - X1)))

    # The sea, round the hull and the quay, and its bed far enough down that over the side is
    # a catch. The surface is left out where the hull is, or it is drawn in the engine room.
    m.span(-90, 90, -12.2, -12.0, -60, 70, "sand", (0.3, 0.32, 0.3))
    for (a, b, c, d) in ((-90, -60, 90, -B), (-90, B, 90, 12.0), (-90, -B, X0, B)):
        m.span(a, c, -1.05, -1.0, b, d, "water", None, solid=False)
    x = X1
    while x < 90:
        hw = half_at(x)
        m.span(x, x + 1.0, -1.05, -1.0, -B, -hw, "water", None, solid=False)
        m.span(x, x + 1.0, -1.05, -1.0, hw, B, "water", None, solid=False)
        x += 1.0
    m.span(-90, 90, -1.05, -1.0, 40.0, 70.0, "water", None, solid=False)

    # --- The hull -------------------------------------------------------------
    m.span(X0, X1, E - 0.4, E, -B, B, "metal", hull)
    m.span(X0 - 0.3, X0, E - 0.4, M + 1.1, -B, B, "metal", hull)
    for zs in (-1, 1):
        portholes = [(4.0 * i + 1.6, 4.0 * i + 2.4, 1.2, 1.9) for i in range(1, 18)]
        m.wall((X0, zs * B), (X1, zs * B), "x", "metal", hull, height=H, y0=E, thick=0.3, glass=False)
        m.wall((X0, zs * B), (X1, zs * B), "x", "metal", hull, height=H, y0=L, thick=0.3, holes=portholes)
        gangway = [(40.0, 42.0, 0.0, 1.1), (4.0, 6.0, 0.0, 1.1)] if zs > 0 else []
        m.wall((X0, zs * B), (X1, zs * B), "x", "metal", white, height=1.1, y0=M, thick=0.3, holes=gangway, glass=False)
    # The bow: its sides raked in to a point, decks laid in strips under them.
    rake = math.degrees(math.atan2(B, BOW - X1))
    length = math.hypot(B, BOW - X1)
    for zs in (-1, 1):
        m.box(((X1 + BOW) / 2, (L + M) / 2 - 0.2, zs * B / 2), (length + 0.4, H + 0.4, 0.8), "metal", hull, yaw=zs * rake)
        m.box(((X1 + BOW) / 2, M + 0.55, zs * B / 2), (length + 0.4, 1.1, 0.8), "metal", white, yaw=zs * rake)
    x = X1
    while x < BOW - 0.5:
        hw = half_at(x)
        m.span(x, x + 0.5, E - 0.4, L, -hw, hw, "metal", hull)
        m.span(x, x + 0.5, L - 0.02, L, -hw + 0.4, hw - 0.4, "metal", (0.5, 0.5, 0.5))
        m.span(x, x + 0.5, M - 0.2, M, -hw, hw, "wood", teak)
        x += 0.5
    m.wall((X1, -B), (X1, B), "z", "metal", hull, height=H, y0=E, thick=0.3)

    # --- Decks ----------------------------------------------------------------
    stairs = [(-28.0, -24.0), (16.0, 20.0)]
    holes = [(a, -6.0, b, -1.9) for a, b in stairs]
    m.floor(X0, -B, X1, B, "metal", (0.5, 0.52, 0.5), y=E)
    slab(m, X0, -B, X1, B, L, holes, "carpet", (0.5, 0.25, 0.25))
    slab(m, X0, -B, X1, B, M, holes, "wood", teak)
    slab(m, -32.0, -B, 30.0, B, S, holes, "wood", teak)
    for sx, _ in stairs:
        for y0, y1 in ((E, L), (L, M), (M, S)):
            switchback(m, sx, -1.9, -6.0, y0, y1)
            m.wall((sx, -6.0), (sx, -1.4), "z", "metal", white, height=H, y0=y0)
            m.wall((sx + 4.0, -6.0), (sx + 4.0, -1.4), "z", "metal", white, height=H, y0=y0)
            m.wall((sx, -6.0), (sx + 4.0, -6.0), "x", "metal", white, height=H, y0=y0)
        railing(m, sx, -1.9, sx + 2.0, -1.9, S)
        railing(m, sx, -6.0, sx, -1.9, S)
        railing(m, sx + 4.0, -6.0, sx + 4.0, -1.9, S)
        railing(m, sx, -6.0, sx + 4.0, -6.0, S)

    # --- The engine room --------------------------------------------------------
    for (ex, ez) in ((-20.0, -4.5), (-20.0, 4.5), (-8.0, -4.5), (-8.0, 4.5)):
        m.span(ex - 4.0, ex + 4.0, E, E + 2.2, ez - 1.4, ez + 1.4, "metal", (0.35, 0.4, 0.38))
        m.span(ex - 3.6, ex + 3.6, E + 2.2, E + 2.5, ez - 0.6, ez + 0.6, "metal", (0.5, 0.2, 0.15))
    for gx in (4.0, 10.0, 16.0):
        m.span(gx - 1.6, gx + 1.6, E, E + 1.8, 3.4, 6.6, "metal", (0.55, 0.5, 0.2))
    crawl(m, 2.0, -7.6, 18.0, -6.2, "x", y=E, mat="metal", tint=(0.4, 0.42, 0.45))
    for zz in (-8.4, 8.4):
        m.span(X0 + 2, 30.0, E + 2.4, E + 2.7, zz - 0.15, zz + 0.15, "metal", (0.6, 0.3, 0.2))
    lights_over(m, -30.0, -B, X1, B, L - 0.2, energy=0.8, colour=(1.0, 0.88, 0.7))
    # The steering-gear room behind the after bulkhead: its door is a panel of the bulkhead.
    m.wall((-32.0, -B), (-32.0, B), "z", "metal", (0.45, 0.48, 0.46), height=H, y0=E,
           holes=[(12.0, 13.0, 0.0, 2.1, {"name": "panel", "secret": True, "hinge": "end"})])
    m.span(-38.0, -35.0, E, E + 1.6, -1.0, 1.0, "metal", (0.5, 0.2, 0.15))
    m.prop("furniture_cardboard_box_closed", -34.0, -6.0, 20.0, y=E)
    m.light(-36.0, L - 0.6, 0.0, rng=6.0, energy=0.4, colour=(1.0, 0.6, 0.4))

    # --- The cabin deck -----------------------------------------------------------
    for zs in (-1, 1):
        m.wall((X0, zs * 1.4), (X1, zs * 1.4), "x", "paint", inside, height=H, y0=L,
               holes=[(4.0 * i + 0.4, 4.0 * i + 1.8, 0.0, DOOR_H, {"name": "cabin door", "into": "-" if zs < 0 else "+", "tint": (0.45, 0.32, 0.22)})
                      for i in range(19) if not (zs < 0 and X0 + 4.0 * i in (-28.0, 16.0)) and X0 + 4.0 * i not in (-40.0, -36.0)] +
                     ([(12.0, 16.0, 0.0, H), (56.0, 60.0, 0.0, H)] if zs < 0 else []))
        for i in range(19):
            cx = X0 + 4.0 * i
            if cx in (-40.0, -36.0):
                continue
            if not (zs < 0 and cx == 16.0):
                m.wall((cx, zs * 1.4), (cx, zs * (B - 0.15)), "z", "paint", inside, height=H, y0=L)
            if zs < 0 and cx in (-28.0, 16.0):
                continue
            zb = zs * (B - 0.15)
            m.against("furniture_bed_single", "e", cx + 3.9, zs * 5.6, y=L)
            m.against("furniture_bookcase_closed_doors", "w", cx + 0.1, zs * 6.6, y=L)
            m.prop("furniture_cabinet_bed_drawer", cx + 3.4, zs * 7.9, 0.0 if zs < 0 else 180.0, y=L)
            m.prop("furniture_rug_rectangle", cx + 2.0, zs * 4.0, y=L)
    m.wall((X0, -1.4), (X0, 1.4), "z", "metal", hull, height=H, y0=L)
    m.wall((X1, -B), (X1, B), "z", "paint", inside, height=H, y0=L,
           holes=[(7.6, 9.0, 0.0, DOOR_H, {"name": "store door", "into": "+", "away": False, "mat": "metal", "tint": (0.5, 0.52, 0.55)})])
    for (bx, bz) in ((39.0, -2.0), (39.6, 1.4), (41.0, -0.4), (38.4, 3.0)):
        m.prop("furniture_cardboard_box_closed", bx, bz, bx * 13, y=L)
    m.span(37.0, 39.0, L, L + 1.2, -5.0, -3.6, "wood", (0.6, 0.45, 0.3))
    lights_over(m, X0, -1.4, X1, 1.4, M - 0.2, energy=0.8)
    for i in range(19):
        cx = X0 + 4.0 * i
        for zs in (-1, 1):
            if (zs < 0 and cx in (-28.0, 16.0)) or cx in (-40.0, -36.0):
                continue
            m.light(cx + 2.0, M - 0.6, zs * 5.0, rng=5.0, energy=0.6)
    m.span(-40.0, -32.0, L, M - 0.2, -B, -1.4, "metal", hull)
    m.span(-40.0, -32.0, L, M - 0.2, 1.4, B, "metal", hull)

    # --- The main deck: saloons inside, promenades outside -------------------------
    SX0, SX1, SZ = -32.0, 30.0, 6.0
    for zs in (-1, 1):
        windows = [(x0 + 1.0, x0 + 3.0, 0.9, 2.3) for x0 in frange(9.0, 52.0, 4.0)]
        doors = [(1.0, 2.4, 0.0, DOOR_H, {"name": "promenade door", "into": "-" if zs > 0 else "+", "tint": (0.45, 0.32, 0.22)}),
                 (55.0, 56.4, 0.0, DOOR_H, {"name": "promenade door", "into": "-" if zs > 0 else "+", "tint": (0.45, 0.32, 0.22)})]
        m.wall((SX0, zs * SZ), (SX1, zs * SZ), "x", "paint", white, height=H, y0=M, holes=doors + windows, inner="paint", inner_tint=inside)
    m.wall((SX0, -SZ), (SX0, SZ), "z", "paint", white, height=H, y0=M,
           holes=[(4.6, 7.4, 0.0, DOOR_H, {"name": "aft doors", "leaves": 2, "into": "-", "away": False, "angle": 88.0, "tint": (0.45, 0.32, 0.22)})])
    m.wall((SX1, -SZ), (SX1, SZ), "z", "paint", white, height=H, y0=M,
           holes=[(4.6, 7.4, 0.0, DOOR_H, {"name": "forward doors", "leaves": 2, "into": "+", "away": False, "angle": 88.0, "tint": (0.45, 0.32, 0.22)})])
    for wx, name in ((-24.0, "dining room doors"), (-4.0, "lounge doors"), (14.0, "lobby doors")):
        m.wall((wx, -SZ), (wx, SZ), "z", "paint", inside, height=H, y0=M,
               holes=[(4.6, 7.4, 0.0, DOOR_H, {"name": name, "leaves": 2, "into": "+", "tint": (0.45, 0.32, 0.22)})])
    for (a, b) in ((SX0, -24.0), (-24.0, -4.0), (-4.0, 14.0), (14.0, SX1)):
        lights_over(m, a, -SZ, b, SZ, S - 0.2, energy=1.0, colour=(1.0, 0.9, 0.78))
    # The dining room.
    for tx in (-21.0, -16.0, -11.0, -6.5):
        for tz in (-3.5, 0.0, 3.5):
            t = m.prop("furniture_table_cloth", tx, tz, 90.0, y=M)
            m.prop("furniture_chair_cushion", tx - 0.85, tz, 90.0, y=M)
            m.prop("furniture_chair_cushion", tx + 0.85, tz, -90.0, y=M)
    # The lounge and its bar.
    for i in range(5):
        m.prop("furniture_kitchen_bar", 1.0 + i, -5.4, 0.0, y=M)
        m.prop("furniture_stool_bar", 1.0 + i, -4.5, 0.0, y=M)
    m.against("furniture_kitchen_fridge_large", "e", 13.85, -4.6, y=M)
    for (sx_, sz_, yaw) in ((0.0, 2.0, 90.0), (6.0, 4.6, 180.0), (10.5, 1.0, -90.0)):
        m.prop("furniture_lounge_sofa", sx_, sz_, yaw, y=M)
    m.prop("furniture_table_coffee", 3.2, 2.0, y=M)
    m.prop("furniture_table_coffee", 8.0, 2.0, y=M)
    m.prop("furniture_rug_round", 5.0, 2.0, y=M)
    m.prop("furniture_potted_plant", -3.4, -5.4, y=M)
    # The lobbies: the stairs, the purser's desk, benches, plants.
    m.prop("furniture_desk", 17.0, 4.6, 180.0, y=M)
    m.prop("furniture_chair_desk", 17.0, 5.4, 180.0, y=M)
    m.prop("furniture_bench_cushion", 28.6, 2.0, -90.0, y=M)
    m.prop("furniture_potted_plant", 29.2, 5.4, y=M)
    m.prop("furniture_bench_cushion", -25.0, 4.6, 180.0, y=M)
    m.prop("furniture_potted_plant", -31.4, 5.4, y=M)
    # The promenades: deck chairs and life rings, the covered side of the sun deck over them.
    for zs in (-1, 1):
        for x in frange(-28.0, 28.0, 7.0):
            m.prop("furniture_lounge_chair_relax", x, zs * 7.6, 0.0 if zs > 0 else 180.0, y=M)
        for x in frange(-30.0, 30.0, 12.0):
            m.light(x, S - 0.5, zs * 7.5, rng=7.0, energy=0.6)

    # Aft and forward open decks: bollards, coils, and the stairs up to the sun deck.
    for (bx, bz) in ((-38.0, -6.0), (-38.0, 6.0), (40.0, -2.0), (40.0, 2.0)):
        m.span(bx - 0.3, bx + 0.3, M, M + 0.7, bz - 0.3, bz + 0.3, "metal", (0.2, 0.2, 0.22))
    flight(m, -39.6, -32.0, 6.4, 8.6, M, S, "e", "wood", teak, thick=0.2)
    flight(m, 30.0, 37.6, -8.6, -6.4, M, S, "w", "wood", teak, thick=0.2)
    m.prop("furniture_cardboard_box_closed", -36.0, -3.0, 15.0, y=M)
    m.prop("furniture_cardboard_box_closed", -35.4, -2.6, 50.0, y=M)
    m.span(41.5, 43.5, M, M + 0.9, -0.8, 0.8, "metal", (0.3, 0.3, 0.3))

    # --- The sun deck ---------------------------------------------------------------
    for zs in (-1, 1):
        railing(m, -32.0, zs * B, 30.0, zs * B, S, height=1.1, mat="metal", tint=white)
    railing(m, -32.0, -B, -32.0, 6.4, S, height=1.1, tint=white)
    railing(m, 30.0, -6.4, 30.0, B, S, height=1.1, tint=white)
    # The pool, raised, with water a crouching person sits under the edge of.
    m.span(-8.0, 2.0, S, S + 1.0, -3.2, -3.0, "tile_small", (0.85, 0.92, 0.95))
    m.span(-8.0, 2.0, S, S + 1.0, 3.0, 3.2, "tile_small", (0.85, 0.92, 0.95))
    m.span(-8.0, -7.8, S, S + 1.0, -3.0, 3.0, "tile_small", (0.85, 0.92, 0.95))
    m.span(1.8, 2.0, S, S + 1.0, -3.0, 3.0, "tile_small", (0.85, 0.92, 0.95))
    m.span(-7.8, 1.8, S + 0.75, S + 0.8, -3.0, 3.0, "water", None, solid=False)
    flight(m, 2.0, 2.9, -0.6, 0.6, S, S + 1.0, "w", "tile_small", (0.85, 0.92, 0.95), thick=0.2)
    for x in (-14.0, -11.5, 5.5, 8.0):
        for zs in (-1, 1):
            m.prop("furniture_lounge_chair_relax", x, zs * 4.4, 90.0 if zs > 0 else -90.0, y=S)
    stall(m, 12.0, 2.0, (0.2, 0.5, 0.6), y=S)
    # The funnel, the lifeboats along both rails, the bridge forward and its roof.
    m.span(-22.0, -16.0, S, S + 7.0, -2.6, 2.6, "metal", (0.85, 0.3, 0.2))
    m.span(-22.0, -16.0, S + 7.0, S + 7.8, -2.6, 2.6, "metal", (0.15, 0.15, 0.15))
    for x in (-26.0, -9.0, 8.0):
        lifeboat(m, x, -7.5, S)
        lifeboat(m, x, 7.5, S)
    bx0, bx1 = 22.0, 30.0
    m.wall((bx0, -5.0), (bx1, -5.0), "x", "paint", white, height=2.8, y0=S, holes=[(1.0, 2.4, 0.0, DOOR_H, {"name": "bridge door", "into": "+"})])
    m.wall((bx0, 5.0), (bx1, 5.0), "x", "paint", white, height=2.8, y0=S, holes=[(5.0, 6.4, 0.0, DOOR_H, {"name": "bridge door", "into": "-"})])
    m.wall((bx0, -5.0), (bx0, 5.0), "z", "paint", white, height=2.8, y0=S, holes=[(1.0, 3.0, 1.0, 2.2), (7.0, 9.0, 1.0, 2.2)])
    m.wall((bx1, -5.0), (bx1, 5.0), "z", "paint", white, height=2.8, y0=S, holes=[(0.6, 9.4, 1.0, 2.4)])
    m.span(bx0 - 0.2, bx1 + 0.2, S + 2.8, S + 3.0, -5.2, 5.2, "metal", white)
    for zz in frange(-3.5, 3.6, 1.0):
        bar = m.prop("furniture_kitchen_bar", 28.9, zz, -90.0, y=S)
    m.on("furniture_computer_screen", bar, yaw=-90.0)
    m.prop("furniture_chair_desk", 27.6, 0.0, -90.0, y=S)
    m.prop("furniture_desk", 24.0, -3.4, 0.0, y=S)
    m.light(26.0, S + 2.3, 0.0, rng=7.0, energy=0.8)
    # Up to the bridge's roof: a stair along its west end, arriving through a gap in the rail.
    flight(m, 14.6, bx0 - 0.2, 3.8, 5.2, S, S + 3.0, "e", "metal", white, thick=0.2)
    railing(m, bx0, -5.2, bx1, -5.2, S + 3.0, tint=white)
    railing(m, bx0, 5.2, bx1, 5.2, S + 3.0, tint=white)
    railing(m, bx1, -5.2, bx1, 5.2, S + 3.0, tint=white)
    railing(m, bx0, -5.2, bx0, 3.7, S + 3.0, tint=white)
    m.span(25.4, 25.6, S + 3.0, S + 6.0, -0.1, 0.1, "metal", white)

    # --- The quay ---------------------------------------------------------------
    QY = 2.0
    m.span(-90, 90, -12.0, QY, 12.0, 40.0, "concrete", (0.6, 0.6, 0.58))
    for (gx0, gx1) in ((-36.0, -34.0), (0.0, 2.0)):
        # A gangway: steps up from the quay, then a plate across the water at the deck's height.
        m.span(gx0, gx1, M - 0.08, M, 9.0, 12.4, "metal", (0.5, 0.52, 0.55))
        flight(m, gx0, gx1, 12.4, 15.0, QY, M, "n", "metal", (0.5, 0.52, 0.55), thick=0.15)
        railing(m, gx0, 9.2, gx0, 12.4, M, height=1.0)
        railing(m, gx1, 9.2, gx1, 12.4, M, height=1.0)
    m.span(-90, 90, QY, QY + 0.08, 12.0, 12.4, "metal", (0.9, 0.75, 0.2), solid=False)
    container(m, -24.0, 22.0, "x", y=QY, open_end="e", tint=(0.2, 0.35, 0.6))
    container(m, -24.0, 24.6, "x", y=QY, open_end="e", tint=(0.6, 0.2, 0.18), doors="open")
    container(m, 14.0, 26.0, "z", y=QY, open_end="s", tint=(0.3, 0.5, 0.35))
    for (cx, cz) in ((-6.0, 30.0), (-4.6, 30.0), (-5.3, 30.0), (24.0, 18.0)):
        m.span(cx - 0.6, cx + 0.6, QY, QY + 1.2, cz - 0.6, cz + 0.6, "wood", (0.66, 0.5, 0.34))
    room_at(m, 30.0, 26.0, 40.0, 34.0, QY, "tile", None, height=3.0, lights=1)
    for zz, holes in ((26.0, [(2.0, 3.4, 0.0, DOOR_H, {"name": "ticket office door", "into": "+"}), (5.0, 8.0, 1.0, 2.2)]), (34.0, [])):
        m.wall((30.0, zz), (40.0, zz), "x", "brick", None, height=3.0, y0=QY, thick=OUTER, holes=holes)
    m.wall((30.0, 26.0), (30.0, 34.0), "z", "brick", None, height=3.0, y0=QY, thick=OUTER)
    m.wall((40.0, 26.0), (40.0, 34.0), "z", "brick", None, height=3.0, y0=QY, thick=OUTER)
    for (bx, bz) in ((-30.0, 13.0), (-10.0, 13.0), (10.0, 13.0), (30.0, 13.0)):
        m.span(bx - 0.3, bx + 0.3, QY, QY + 0.7, bz - 0.3, bz + 0.3, "metal", (0.2, 0.2, 0.22))
    # The quay's fence on three sides; the fourth is the water.
    for (a, b, c, d) in ((-60.4, 12.0, -60.0, 40.4), (60.0, 12.0, 60.4, 40.4), (-60.4, 40.0, 60.4, 40.4)):
        m.span(a, c, QY, QY + 2.6, b, d, "metal", (0.4, 0.42, 0.45))

    for (x, z) in ((-17.5, 7.6), (-10.5, 7.6), (-3.5, -7.6), (10.5, -7.6), (-13.5, 0.0), (6.0, -1.0), (17.5, 7.6), (-24.5, -7.6)):
        m.spawn("hiders", x, z, y=M + 0.05)
    for (x, z) in ((-8.0, 20.0), (-4.0, 20.0), (4.0, 20.0), (8.0, 20.0)):
        m.spawn("seekers", x, z, y=QY + 0.05)
    return m.build()


def crenellate(m, a, b, along, fixed, y, thick, mat="stone", tint=None, step=1.6, height=1.0):
    """Merlons along an edge: blocks `step / 2` long every `step`, on a wall top at `y`, from a to b
    along `along` with their middle on `fixed`. A wall walk's parapet, a tower's top."""
    x = a
    while x + step / 2 <= b + 0.01:
        if along == "x":
            m.span(x, x + step / 2, y, y + height, fixed - thick / 2, fixed + thick / 2, mat, tint)
        else:
            m.span(fixed - thick / 2, fixed + thick / 2, y, y + height, x, x + step / 2, mat, tint)
        x += step


def castle():
    """Ravenhold Castle: a curtain wall with a walk along its top and a tower at each corner, a
    gatehouse with a portcullis over a moat, a keep of three floors over a dungeon, stables,
    a smithy and a well in the courtyard.

    The walls are the way round: up any tower, along the walk, down another, or down the stairs
    against the inside of the east and west walls. The keep is the way up, to its roof. And the
    dungeon is the way out that nobody sees: a panel in the guard room's wall opens on a tunnel
    under the courtyard that comes up through the floor of the stables' tack room."""
    m = Map(
        "hs_castle", "Ravenhold Castle",
        "A walled castle with four towers, a keep over a dungeon, and a tunnel nobody is meant to know about.",
        {"sky_top": [0.38, 0.5, 0.72], "sky_horizon": [0.82, 0.8, 0.76], "ground": [0.3, 0.32, 0.26],
         "sun_pitch": -38, "sun_yaw": 230, "sun_energy": 1.0, "sun_colour": [1.0, 0.92, 0.8],
         "ambient": [0.58, 0.6, 0.66], "ambient_energy": 0.5, "fog": 0.0018},
        kill_y=-10.0,
    )
    grey = (0.72, 0.7, 0.66)
    dark = (0.55, 0.53, 0.5)
    oak = (0.45, 0.32, 0.22)
    W = 31.0          # the curtain wall's centre line
    WT = 2.0          # its thickness
    WH = 6.0          # its height: the walk is on top
    T = 32.0          # the towers' centres
    TH = 9.0
    MOAT = (36.0, 42.0)

    # --- The ground: the field round the moat, the moat, the berm, the courtyard ----------
    m.floor_around(-64, -64, 64, 64, (-MOAT[1], -MOAT[1], MOAT[1], MOAT[1]), "grass", y=GROUND)
    for (a, b, c, d) in ((-MOAT[1], -MOAT[1], MOAT[1], -MOAT[0]), (-MOAT[1], MOAT[0], MOAT[1], MOAT[1]),
                         (-MOAT[1], -MOAT[0], -MOAT[0], MOAT[0]), (MOAT[0], -MOAT[0], MOAT[1], MOAT[0])):
        m.span(a, c, -1.8, -1.6, b, d, "dirt", (0.4, 0.36, 0.3))
        m.span(a, c, -0.65, -0.6, b, d, "water", None, solid=False)
    m.floor_around(-MOAT[0], -MOAT[0], MOAT[0], MOAT[0], (-30.0, -30.0, 30.0, 30.0), "grass", (0.85, 0.9, 0.8), y=GROUND)
    for (a, b, c, d, way) in ((-MOAT[1], -1.0, -40.8, 1.0, "w"), (40.8, -1.0, MOAT[1], 1.0, "e"),
                              (-1.0, -MOAT[1], 1.0, -40.8, "n"), (20.0, 40.8, 22.0, MOAT[1], "s")):
        flight(m, a, c, b, d, -1.6, 0.0, way, "stone", dark, thick=0.25)
    boundary(m, -64, -64, 64, 64, height=2.4, mat="hedge", tint=(0.8, 1.0, 0.8))
    m.overlay(-2.0, MOAT[1], 2.0, 64.0, "dirt", (0.7, 0.6, 0.45))

    # The courtyard's ground, with the keep's footprint and the tunnel's stair left out of it.
    tunnel_hole = (-11.5, 3.0, -9.9, 10.5)
    slab(m, -30.0, -30.0, 30.0, 30.0, 0.0, [(-12.0, -26.0, 12.0, -8.0), tunnel_hole], "dirt", (0.75, 0.68, 0.55))

    # --- The curtain wall, its walk and its merlons --------------------------------------
    m.wall((-28.0, -W), (28.0, -W), "x", "stone", grey, height=WH, thick=WT, glass=False,
           holes=[(42.0, 43.4, 0.0, DOOR_H, {"name": "postern", "into": "-", "away": False, "tint": oak})])
    m.wall((-28.0, W), (28.0, W), "x", "stone", grey, height=WH, thick=WT, glass=False,
           holes=[(24.0, 32.0, 0.0, 4.0, {"name": "castle gate", "leaves": 2, "into": "-", "away": False, "open": True, "angle": 85.0, "tint": oak, "seconds": 1.6})])
    m.wall((-W, -28.0), (-W, 28.0), "z", "stone", grey, height=WH, thick=WT, glass=False)
    m.wall((W, -28.0), (W, 28.0), "z", "stone", grey, height=WH, thick=WT, glass=False)
    for sgn in (-1, 1):
        crenellate(m, -28.0, 28.0, "x", sgn * (W + WT / 2 - 0.25), WH, 0.5, tint=grey)
        crenellate(m, -28.0, 28.0, "z", sgn * (W + WT / 2 - 0.25), WH, 0.5, tint=grey)
    # Stairs up the inside of the east and west walls to the walk.
    flight(m, W - WT / 2 - 2.0, W - WT / 2, -6.0, 7.0, 0.0, WH, "n", "stone", dark, thick=0.3)
    flight(m, -W + WT / 2, -W + WT / 2 + 2.0, -7.0, 6.0, 0.0, WH, "s", "stone", dark, thick=0.3)
    # A landing at the top of each, beside the walk, with a rail across its far end: the top
    # step is otherwise a drop.
    m.span(W - WT / 2 - 2.0, W - WT / 2, WH - 0.3, WH, -9.0, -6.0, "stone", dark)
    railing(m, W - WT / 2 - 2.0, -9.0, W - WT / 2, -9.0, WH)
    railing(m, W - WT / 2 - 2.0, -9.0, W - WT / 2 - 2.0, -6.0, WH)
    m.span(-W + WT / 2, -W + WT / 2 + 2.0, WH - 0.3, WH, 6.0, 9.0, "stone", dark)
    railing(m, -W + WT / 2, 9.0, -W + WT / 2 + 2.0, 9.0, WH)
    railing(m, -W + WT / 2 + 2.0, 6.0, -W + WT / 2 + 2.0, 9.0, WH)
    # The gatehouse: two half-towers outside the gate, the passage roofed between them, and the
    # portcullis at its outer end, up.
    for gx in (-8.0, 4.0):
        m.span(gx, gx + 4.0, 0.0, 8.0, W + WT / 2, MOAT[0], "stone", grey)
        crenellate(m, gx, gx + 4.0, "x", MOAT[0] - 0.25, 8.0, 0.5, tint=grey)
    m.span(-4.0, 4.0, 4.4, WH, W + WT / 2, MOAT[0], "stone", grey)
    m.door("x", -4.0, 4.0, MOAT[0] - 0.6, 0.0, 4.4, name="portcullis", motion="lift", travel=3.8, open=True,
           mat="metal", tint=(0.25, 0.25, 0.27), seconds=2.0)
    m.span(-4.0, 4.0, -0.2, 0.0, MOAT[0], MOAT[1], "wood", oak)
    m.span(14.0, 15.4, -0.2, 0.0, -MOAT[1], -MOAT[0], "wood", oak)
    m.light(0.0, 3.6, 33.5, rng=6.0, energy=0.7, colour=(1.0, 0.8, 0.55))

    # --- The towers: a switchback inside each, from the courtyard to the walk to the top -----
    for cx in (-T, T):
        for cz in (-T, T):
            sx_, sz_ = (1 if cx > 0 else -1), (1 if cz > 0 else -1)
            x0, x1, z0, z1 = cx - 4.0, cx + 4.0, cz - 4.0, cz + 4.0
            sx = cx - 3.2 if cx < 0 else cx - 0.8
            inner_x = cx - sx_ * 4.0          # the face towards the courtyard in x
            inner_z = cz - sz_ * 4.0
            # Doors: the courtyard at the foot, the two walks at 6.
            def along_z(z_lo, z_hi):
                return (z_lo - z0, z_hi - z0)
            def along_x(x_lo, x_hi):
                return (x_lo - x0, x_hi - x0)
            gz = along_z(*sorted((cz - sz_ * 2.0, cz - sz_ * 3.4)))
            wz = along_z(*sorted((cz - sz_ * 0.0, cz - sz_ * 2.0)))
            wx = along_x(*sorted((cx - sx_ * 0.0, cx - sx_ * 2.0)))
            slits = [(1.0, 1.4, 1.2, 2.4), (6.6, 7.0, 4.2, 5.4)]
            tdoor = {"name": "tower door", "into": "+" if sx_ < 0 else "-", "hinge": "end" if sz_ < 0 else "start", "tint": oak}
            for face_x in (x0, x1):
                holes = list(slits) if face_x != inner_x else []
                if face_x == inner_x:
                    holes = [(gz[0], gz[1], 0.0, DOOR_H, tdoor), (wz[0], wz[1], WH, WH + DOOR_H)]
                m.wall((face_x, z0), (face_x, z1), "z", "stone", grey, height=TH, thick=0.6, holes=holes, glass=False)
            for face_z in (z0, z1):
                holes = list(slits) if face_z != inner_z else [(wx[0], wx[1], WH, WH + DOOR_H)]
                m.wall((x0, face_z), (x1, face_z), "x", "stone", grey, height=TH, thick=0.6, holes=holes, glass=False)
            zf, zb = cz + 3.2, cz - 3.2
            for y0 in (0.0, 3.0, 6.0):
                switchback(m, sx, zf, zb, y0, y0 + 3.0, "stone", dark, run=2.4)
            for y in (3.0, 6.0, TH):
                slab(m, x0 + 0.3, z0 + 0.3, x1 - 0.3, z1 - 0.3, y, [(sx, zb, sx + 4.0, zf)], "stone", dark)
            railing(m, sx, zb, sx + 4.0, zb, TH, mat="metal")
            railing(m, sx, zb, sx, zf, TH, mat="metal")
            railing(m, sx + 4.0, zb, sx + 4.0, zf, TH, mat="metal")
            railing(m, sx, zf, sx + 2.0, zf, TH, mat="metal")
            for sgn in (-1, 1):
                crenellate(m, x0, x1, "x", cz + sgn * 3.75, TH, 0.5, tint=grey)
                crenellate(m, z0 + 0.5, z1 - 0.5, "z", cx + sgn * 3.75, TH, 0.5, tint=grey)
            m.light(cx, 2.5, cz, rng=5.0, energy=0.5, colour=(1.0, 0.8, 0.55))
            m.light(cx, 5.5, cz, rng=5.0, energy=0.5, colour=(1.0, 0.8, 0.55))

    # --- The keep: three floors and a roof over the dungeon -------------------------------
    KX0, KX1, KZ0, KZ1 = -12.0, 12.0, -26.0, -8.0
    KH = 4.0
    stair = (-11.4, -25.4, -7.4, -19.0)
    down = (3.4, -18.0, 5.4, -12.0)
    slab(m, KX0, KZ0, KX1, KZ1, 0.0, [down], "stone", (0.6, 0.58, 0.55))
    for y in (KH, 2 * KH, 3 * KH):
        slab(m, KX0, KZ0, KX1, KZ1, y, [stair], "wood" if y < 3 * KH else "stone", oak if y < 3 * KH else dark)
    for k, y0 in enumerate((0.0, KH, 2 * KH)):
        switchback(m, stair[0], stair[3], stair[1], y0, y0 + KH, "stone", dark, run=2.4)
        slit = [(x, x + 0.5, 1.2, 2.8) for x in (3.0, 9.0, 15.0, 21.0)]
        front = list(slit)
        if k == 0:
            front = [(3.0, 3.5, 1.2, 2.8), (10.6, 13.4, 0.0, 2.8, {"name": "keep doors", "leaves": 2, "into": "-", "away": False, "angle": 85.0, "tint": oak}), (21.0, 21.5, 1.2, 2.8)]
        m.wall((KX0, KZ1), (KX1, KZ1), "x", "stone", grey, height=KH, y0=y0, thick=1.0, holes=front, glass=False)
        m.wall((KX0, KZ0), (KX1, KZ0), "x", "stone", grey, height=KH, y0=y0, thick=1.0, holes=slit, glass=False)
        m.wall((KX0, KZ0), (KX0, KZ1), "z", "stone", grey, height=KH, y0=y0, thick=1.0, holes=[(6.0, 6.5, 1.2, 2.8), (12.0, 12.5, 1.2, 2.8)], glass=False)
        east = [(6.0, 6.5, 1.2, 2.8), (14.0, 15.4, 0.0, DOOR_H, {"name": "kitchen door", "into": "-", "tint": oak})] if k == 0 else [(6.0, 6.5, 1.2, 2.8), (12.0, 12.5, 1.2, 2.8)]
        m.wall((KX1, KZ0), (KX1, KZ1), "z", "stone", grey, height=KH, y0=y0, thick=1.0, holes=east, glass=False)
        if k < 2:
            m.wall((6.0 if k == 0 else 0.0, KZ0), (6.0 if k == 0 else 0.0, KZ1), "z", "plaster", (0.85, 0.8, 0.7), height=KH, y0=y0,
                   holes=[(12.0, 13.4, 0.0, DOOR_H, {"name": "kitchen door" if k == 0 else "library door", "into": "+", "tint": oak})])
        lights_over(m, KX0 + 0.5, KZ0 + 0.5, KX1 - 0.5, KZ1 - 0.5, y0 + KH - 0.2, energy=0.9, colour=(1.0, 0.82, 0.6))
    railing(m, stair[0], stair[3], stair[0] + 2.0, stair[3], 3 * KH)
    railing(m, stair[2], stair[1], stair[2], stair[3], 3 * KH)
    railing(m, stair[0], stair[1], stair[2], stair[1], 3 * KH)
    for sgn in (-1, 1):
        crenellate(m, KX0 - 0.5, KX1 + 0.5, "x", (KZ0 if sgn < 0 else KZ1) + sgn * 0.25, 3 * KH, 0.5, tint=grey)
        crenellate(m, KZ0, KZ1, "z", (KX0 if sgn < 0 else KX1) + sgn * 0.25, 3 * KH, 0.5, tint=grey)
    # The great hall: a dais and a chair, long tables, a hearth; the kitchen beyond.
    m.span(-6.0, 4.0, 0.0, 0.4, -25.5, -23.0, "wood", oak)
    m.prop("furniture_lounge_design_chair", -1.0, -24.2, 0.0, y=0.4)
    m.prop("furniture_side_table", 1.0, -24.4, 0.0, y=0.4)
    for tx in (-4.0, 1.0):
        for tz in (-20.0, -16.0, -12.0):
            m.prop("furniture_table_cloth", tx, tz, 90.0)
            m.prop("furniture_bench", tx - 0.9, tz, 90.0)
            m.prop("furniture_bench", tx + 0.9, tz, -90.0)
    m.span(-11.5, -10.5, 0.0, 2.4, -16.0, -12.0, "stone", dark)
    m.span(-11.5, -10.9, 0.0, 0.8, -15.4, -12.6, "dirt", (0.25, 0.2, 0.18), solid=False)
    m.light(-10.2, 0.8, -14.0, rng=5.0, energy=0.9, colour=(1.0, 0.55, 0.25))
    railing(m, down[0], down[1], down[0], down[3], 0.0, mat="wood", tint=oak)
    railing(m, down[0], down[1], down[2], down[1], 0.0, mat="wood", tint=oak)
    m.against("furniture_kitchen_stove", "e", KX1 - 0.5, -24.0)
    m.against("furniture_kitchen_cabinet", "e", KX1 - 0.5, -22.6)
    m.against("furniture_kitchen_fridge", "n", KZ0 + 0.5, 9.0)
    for (bx, bz) in ((8.0, -20.0), (9.0, -20.0), (8.0, -19.0)):
        m.prop("furniture_kitchen_bar", bx, bz, 0.0)
    for i, (bx, bz) in enumerate(((7.0, -10.0), (8.0, -9.6), (10.6, -16.0), (10.6, -17.0))):
        m.span(bx - 0.4, bx + 0.4, 0.0, 1.0, bz - 0.4, bz + 0.4, "wood", (0.55, 0.38, 0.24))
    # The lord's chamber and the library; the barracks over them.
    m.prop("furniture_bed_double", -4.0, -23.8, 0.0, y=KH)
    m.prop("furniture_rug_rectangle", -4.0, -20.6, y=KH)
    m.prop("furniture_desk", -4.0, -10.0, 180.0, y=KH)
    m.prop("furniture_chair_desk", -4.0, -10.9, 0.0, y=KH)
    m.against("furniture_bookcase_closed_doors", "w", KX0 + 0.5, -14.0, y=KH)
    m.prop("furniture_lounge_chair", -9.0, -10.4, 135.0, y=KH)
    for i, bz in enumerate((-24.0, -21.0, -18.0, -15.0)):
        m.prop("furniture_bookcase_closed_wide" if i % 2 else "furniture_bookcase_open", 6.0, bz, 0.0, y=KH)
    m.prop("furniture_table", 6.0, -11.2, 0.0, y=KH)
    m.prop("furniture_chair", 5.4, -10.4, 180.0, y=KH)
    for bx in (-4.0, 0.0, 4.0, 8.0):
        m.prop("furniture_bed_bunk", bx, -24.0, 0.0, y=2 * KH)
        m.prop("furniture_bed_bunk", bx, -10.0, 180.0, y=2 * KH)
    m.prop("furniture_cardboard_box_closed", 10.5, -17.0, 10.0, y=2 * KH)
    m.prop("furniture_cardboard_box_closed", 10.5, -16.2, 40.0, y=2 * KH)

    # --- The dungeon, under the keep ------------------------------------------------------
    DY = -3.5
    m.span(KX0, KX1, DY - 0.2, DY, KZ0, KZ1, "stone", dark)
    m.wall((KX0, KZ0), (KX1, KZ0), "x", "stone", dark, height=-DY, y0=DY, thick=1.0, glass=False)
    m.wall((KX0, KZ1), (KX1, KZ1), "x", "stone", dark, height=-DY, y0=DY, thick=1.0, glass=False,
           holes=[(0.6, 1.6, 0.0, 2.1, {"name": "loose stones", "secret": True, "hinge": "end"})])
    m.wall((KX0, KZ0), (KX0, KZ1), "z", "stone", dark, height=-DY, y0=DY, thick=1.0, glass=False)
    m.wall((KX1, KZ0), (KX1, KZ1), "z", "stone", dark, height=-DY, y0=DY, thick=1.0, glass=False)
    flight(m, down[0], down[2], down[1], down[3], DY, 0.0, "s", "stone", dark, thick=0.25)
    for cx in (-8.0, -4.0, 0.0, 4.0):
        m.wall((cx, KZ0), (cx, -20.0), "z", "stone", dark, height=-DY, y0=DY, glass=False)
    m.wall((KX0, -20.0), (8.0, -20.0), "x", "metal", (0.3, 0.3, 0.32), height=-DY, y0=DY, glass=False,
           holes=[(i * 4.0 + 1.3, i * 4.0 + 2.7, 0.0, 2.1, {"name": "cell door", "into": "-", "away": False, "mat": "metal", "tint": (0.3, 0.3, 0.32)}) for i in range(5)])
    m.wall((8.0, KZ0), (8.0, -20.0), "z", "stone", dark, height=-DY, y0=DY, glass=False)
    for cx in (-10.0, -6.0, -2.0, 2.0, 6.0):
        m.span(cx - 0.9, cx + 0.9, DY, DY + 0.5, KZ0 + 0.5, KZ0 + 1.3, "wood", (0.4, 0.3, 0.22))
        m.light(cx, -0.6, -23.0, rng=4.0, energy=0.25, colour=(1.0, 0.7, 0.4))
    m.prop("furniture_table", -6.0, -11.0, 0.0, y=DY)
    m.prop("furniture_chair", -6.6, -10.2, 180.0, y=DY)
    m.prop("furniture_cardboard_box_closed", 10.0, -10.0, 15.0, y=DY)
    m.prop("furniture_cardboard_box_open", 10.6, -10.8, -20.0, y=DY)
    m.light(-4.0, -0.6, -12.0, rng=8.0, energy=0.5, colour=(1.0, 0.7, 0.4))
    m.light(6.0, -0.6, -16.0, rng=8.0, energy=0.4, colour=(1.0, 0.7, 0.4))
    # The tunnel: south from the guard room's wall, under the courtyard, and up into the tack
    # room through its floor.
    tx0, tx1 = tunnel_hole[0], tunnel_hole[2]
    m.span(tx0, tx1, DY - 0.2, DY, KZ1 + 0.5, tunnel_hole[3], "dirt", (0.35, 0.3, 0.25))
    m.span(tx0 - 0.2, tx0, DY, 0.0, KZ1 + 0.5, tunnel_hole[3], "stone", dark)
    m.span(tx1, tx1 + 0.2, DY, 0.0, KZ1 + 0.5, tunnel_hole[3], "stone", dark)
    m.span(tx0 - 0.2, tx1 + 0.2, -1.2, -1.0, KZ1 + 0.5, tunnel_hole[1], "stone", dark)
    flight(m, tx0, tx1, tunnel_hole[1], tunnel_hole[3], DY, 0.0, "s", "stone", dark, thick=0.25)
    m.light((tx0 + tx1) / 2, -1.5, -2.0, rng=5.0, energy=0.25, colour=(1.0, 0.7, 0.4))
    railing(m, tx0, tunnel_hole[1], tx0, tunnel_hole[3], 0.0, mat="wood", tint=oak)
    railing(m, tx1, tunnel_hole[1], tx1, tunnel_hole[3], 0.0, mat="wood", tint=oak)
    railing(m, tx0, tunnel_hole[1], tx1, tunnel_hole[1], 0.0, mat="wood", tint=oak)

    # --- The courtyard: stables and the tack room, the smithy, the well ---------------------
    SX0, SX1, SZ0, SZ1 = -28.0, -6.0, 0.0, 12.0
    m.span(SX0, SX1, 3.2, 3.3, SZ0, SZ1, "wood", oak)
    m.span(SX0 - 0.3, SX1 + 0.3, 3.3, 3.55, SZ0 - 0.3, SZ1 + 0.3, "roof", (0.45, 0.32, 0.25))
    timber = (0.55, 0.4, 0.28)
    m.wall((SX0, SZ0), (SX1, SZ0), "x", "wood", timber, height=3.2, thick=0.25, glass=False)
    m.wall((SX0, SZ1), (SX1, SZ1), "x", "wood", timber, height=3.2, thick=0.25,
           holes=[(6.0, 10.0, 0.0, 2.8, {"name": "stable doors", "leaves": 2, "into": "+", "away": False, "open": True, "tint": oak}),
                  (19.0, 20.4, 0.0, DOOR_H, {"name": "tack room door", "into": "+", "away": False, "tint": oak})])
    m.wall((SX0, SZ0), (SX0, SZ1), "z", "wood", timber, height=3.2, thick=0.25, glass=False)
    m.wall((SX1, SZ0), (SX1, SZ1), "z", "wood", timber, height=3.2, thick=0.25, glass=False)
    m.wall((-13.0, SZ0), (-13.0, SZ1), "z", "wood", timber, height=3.2, thick=0.2,
           holes=[(9.0, 10.4, 0.0, DOOR_H, {"name": "tack room door", "into": "-", "away": False, "tint": oak})])
    for i in range(4):
        a = SX0 + i * 3.75
        if i > 0:
            m.span(a - 0.06, a + 0.06, 0.0, 1.6, SZ0, 4.0, "wood", timber)
        m.door("x", a + 0.3, a + 3.45, 4.0, 0.0, 1.4, name="stall gate", hinge="start", into="+", away=False, angle=90.0, mat="wood", tint=oak)
        m.span(a + 0.5, a + 1.7, 0.0, 0.8, SZ0 + 0.4, SZ0 + 1.2, "sand", (0.95, 0.82, 0.45))
    m.span(-13.0 - 0.06, -13.0 + 0.06, 0.0, 1.6, SZ0, 4.0, "wood", timber)
    for (hx, hz, lv) in ((-26.0, 10.6, 0), (-24.8, 10.6, 0), (-25.4, 10.6, 1), (-15.0, 10.4, 0)):
        m.span(hx - 0.6, hx + 0.6, lv * 0.8, (lv + 1) * 0.8, hz - 0.4, hz + 0.4, "sand", (0.95, 0.82, 0.45))
    m.light(-20.0, 2.6, 7.0, rng=9.0, energy=0.6, colour=(1.0, 0.85, 0.6))
    m.light(-8.0, 2.6, 6.0, rng=6.0, energy=0.5, colour=(1.0, 0.85, 0.6))
    m.prop("furniture_cardboard_box_closed", -7.0, 1.0, 10.0)
    m.prop("furniture_cardboard_box_closed", -7.2, 2.0, 40.0)
    m.against("furniture_bookcase_closed", "e", SX1 - 0.15, 6.0)
    # The smithy: a roof on posts, a forge and an anvil.
    m.span(14.0, 24.0, 3.0, 3.15, 2.0, 10.0, "roof", (0.4, 0.3, 0.25))
    for px in (14.1, 23.9):
        for pz in (2.1, 9.9):
            m.span(px - 0.12, px + 0.12, 0.0, 3.0, pz - 0.12, pz + 0.12, "wood", timber)
    m.span(20.0, 23.6, 0.0, 1.0, 2.4, 4.4, "stone", dark)
    m.span(20.6, 23.0, 1.0, 1.1, 2.8, 4.0, "dirt", (0.3, 0.1, 0.05), solid=False)
    m.light(21.8, 1.6, 3.4, rng=5.0, energy=0.8, colour=(1.0, 0.5, 0.2))
    m.span(17.0, 18.0, 0.0, 0.8, 5.6, 6.2, "metal", (0.2, 0.2, 0.22))
    for (bx, bz) in ((15.0, 9.0), (15.9, 9.2), (15.4, 8.3)):
        m.span(bx - 0.4, bx + 0.4, 0.0, 1.0, bz - 0.4, bz + 0.4, "wood", (0.55, 0.38, 0.24))
    # The well.
    for (a, b, c, d) in ((4.0, 2.0, 7.0, 2.4), (4.0, 4.6, 7.0, 5.0), (4.0, 2.4, 4.4, 4.6), (6.6, 2.4, 7.0, 4.6)):
        m.span(a, c, 0.0, 0.9, b, d, "stone", grey)
    m.span(4.4, 6.6, -0.2, 0.05, 2.4, 4.6, "water", None, solid=False)
    m.span(4.4, 6.6, -2.2, -2.0, 2.4, 4.6, "stone", dark)
    for px in (4.1, 6.9):
        m.span(px - 0.08, px + 0.08, 0.9, 2.4, 3.42, 3.58, "wood", timber)
    m.span(4.0, 7.0, 2.4, 2.6, 3.0, 4.0, "roof", (0.4, 0.3, 0.25))
    # Carts, barrels, haystacks, training posts, market stalls by the gate, trees.
    for (cx, cz) in ((-18.0, 20.0), (10.0, 18.0)):
        m.span(cx - 1.2, cx + 1.2, 0.5, 1.2, cz - 0.8, cz + 0.8, "wood", (0.55, 0.4, 0.28))
        for wx_ in (cx - 0.9, cx + 0.9):
            m.span(wx_ - 0.05, wx_ + 0.05, 0.0, 0.9, cz - 0.95, cz + 0.95, "wood", (0.3, 0.22, 0.15))
    for (hx, hz, lv) in ((24.0, 20.0, 0), (25.2, 20.0, 0), (24.6, 20.0, 1), (-24.0, -20.0, 0), (-22.8, -20.0, 0)):
        m.span(hx - 0.6, hx + 0.6, lv * 0.8, (lv + 1) * 0.8, hz - 0.4, hz + 0.4, "sand", (0.95, 0.82, 0.45))
    for px in (16.0, 18.5, 21.0):
        m.span(px - 0.1, px + 0.1, 0.0, 1.8, -18.1, -17.9, "wood", timber)
        m.span(px - 0.5, px + 0.5, 1.2, 1.35, -18.1, -17.9, "wood", timber)
    stall(m, -8.0, 22.0, (0.6, 0.2, 0.2))
    stall(m, 8.0, 24.0, (0.2, 0.4, 0.6))
    for (tx, tz, kind) in ((-20.0, -14.0, "nature_tree_oak"), (22.0, -6.0, "nature_tree_default"), (-50.0, -50.0, "nature_tree_tall"),
                           (50.0, -48.0, "nature_tree_pine_round_a"), (-48.0, 52.0, "nature_tree_oak_fall"), (52.0, 50.0, "nature_tree_fat"),
                           (-52.0, 10.0, "nature_tree_detailed"), (50.0, -10.0, "nature_tree_default"), (20.0, 54.0, "nature_tree_small")):
        m.prop(kind, tx, tz, (tx * 7) % 90)
    for (bx, bz) in ((-46.0, 30.0), (46.0, 26.0), (-30.0, 50.0), (30.0, -50.0), (-20.0, 26.0), (22.0, -22.0)):
        m.prop("nature_plant_bush_large", bx, bz)

    for (x, z) in ((-12.0, 18.0), (-4.0, 18.0), (4.0, 14.0), (12.0, 14.0), (-16.0, -4.0), (16.0, -4.0), (0.0, -4.0), (-2.0, 26.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((-6.0, 52.0), (-2.0, 52.0), (2.0, 52.0), (6.0, 52.0)):
        m.spawn("seekers", x, z)
    return m.build()


def tent(m, x, z, facing, cloth):
    """A ridge tent 2.4 m wide, 3 deep and 1.6 high, open at the end that faces `facing` ('n' or
    's'): a place to crouch out of sight of everybody but somebody looking straight in."""
    s_ = -1 if facing == "n" else 1
    z0, z1 = (z - 1.5, z + 1.5)
    back = z1 if facing == "n" else z0
    m.span(x - 1.2, x + 1.2, 0.0, 1.2, back - 0.03, back + 0.03, "fabric", cloth)
    m.span(x - 1.25, x - 1.19, 0.0, 1.2, z0, z1, "fabric", cloth)
    m.span(x + 1.19, x + 1.25, 0.0, 1.2, z0, z1, "fabric", cloth)
    # A box cannot pitch, so the roof is stepped in towards a ridge: it reads as a tent from
    # anywhere a person stands, and keeps a crouching one's head covered.
    for (half, y0, y1) in ((1.25, 1.2, 1.32), (0.85, 1.32, 1.46), (0.42, 1.46, 1.6)):
        m.span(x - half, x + half, y0, y1, z0, z1, "fabric", cloth)


def camp():
    """Pinehollow Camp: a summer camp in the woods by a lake. Cabins on blocks, a mess hall,
    tents round a fire, a fire lookout tower, a treehouse and its rope bridge, a boathouse and a
    dock with boats, and a cave in the hillside with a chamber behind a rock that moves.

    Outdoors and wide, like the park, but every part of it goes up or under or in: the tower is
    the highest place on any map here and the most exposed, the treehouse the best view of the
    fire, the cave the darkest, and the cabins have a crawl space under every one."""
    m = Map(
        "hs_camp", "Pinehollow Camp",
        "A summer camp by a lake: cabins, a lookout tower, a treehouse, boats, and a cave with a secret.",
        {"sky_top": [0.36, 0.54, 0.8], "sky_horizon": [0.86, 0.82, 0.72], "ground": [0.28, 0.32, 0.22],
         "sun_pitch": -30, "sun_yaw": 250, "sun_energy": 1.0, "sun_colour": [1.0, 0.88, 0.7],
         "ambient": [0.56, 0.6, 0.62], "ambient_energy": 0.48, "fog": 0.0022},
        kill_y=-10.0,
    )
    wood = (0.58, 0.42, 0.28)
    logs = (0.5, 0.36, 0.24)
    rock = (0.55, 0.53, 0.5)
    LAKE = (20.0, -32.0, 54.0, 6.0)

    m.floor_around(-66, -66, 66, 66, LAKE, "grass", y=GROUND)
    m.span(LAKE[0], LAKE[2], -1.4, -1.2, LAKE[1], LAKE[3], "sand", (0.5, 0.46, 0.36))
    m.span(LAKE[0], LAKE[2], -0.35, -0.3, LAKE[1], LAKE[3], "water", None, solid=False)
    flight(m, LAKE[0], LAKE[0] + 1.6, 0.0, 2.0, -1.2, 0.0, "w", "dirt", (0.5, 0.44, 0.34), thick=0.25)
    flight(m, 40.0, 42.0, LAKE[3] - 1.6, LAKE[3], -1.2, 0.0, "s", "dirt", (0.5, 0.44, 0.34), thick=0.25)
    boundary(m, -66, -66, 66, 66, height=2.6, mat="hedge", tint=(0.7, 0.85, 0.7))
    tree_ring(m, -62, -62, 62, 62, 7.0, ["nature_tree_pine_tall_a", "nature_tree_cone", "nature_tree_pine_round_a", "nature_tree_tall", "nature_tree_default"], 3)
    # Three made to be climbed (game-hide-n-seek's tools/climb_trees.py): limbs to jump and
    # crouch-jump onto, somewhere up for a hider in the woods that is not the treehouse.
    for (tx, tz, kind) in ((-24, 4, "nature_tree_pine_tall_a"), (-14, -4, "nature_tree_cone"), (14, 14, "nature_climbtree_oak"), (30, 26, "nature_climbtree_fork"),
                           (-44, 6, "nature_tree_pine_round_a"), (-6, -40, "nature_tree_tall"), (42, -44, "nature_tree_cone"), (52, 20, "nature_tree_pine_tall_a"),
                           (-50, -14, "nature_climbtree_oak"), (2, 39, "nature_tree_pine_round_a"), (-30, 40, "nature_tree_cone"), (26, 44, "nature_tree_tall"),
                           (14, -48, "nature_tree_pine_tall_a"), (-20, -50, "nature_tree_default"), (44, 34, "nature_tree_oak_fall")):
        m.prop(kind, float(tx), float(tz), (tx * 7 + tz) % 90)
    for (bx, bz, kind) in ((-15, -13, "nature_plant_bush_large"), (14, 4, "nature_plant_bush_detailed"), (-36, 0, "nature_plant_bush_large"),
                           (16, -2, "nature_plant_bush_large"), (36, 14, "nature_plant_bush_detailed"), (-4, 40, "nature_plant_bush_large"),
                           (-46, 28, "nature_rock_large_a"), (48, -38, "nature_rock_tall_a"), (8, 44, "nature_rock_large_a"), (-16, 30, "nature_stump_old"),
                           (34, 30, "nature_log_large"), (-2, -30, "nature_log")):
        m.prop(kind, float(bx), float(bz), (bx * 11) % 90)

    # --- The campfire and the tents round it -----------------------------------------
    m.prop("nature_campfire_logs", 0.0, 0.0)
    m.light(0.0, 1.2, 0.0, rng=8.0, energy=1.0, colour=(1.0, 0.6, 0.3))
    for (lx, lz, yaw) in ((0.0, -3.4, 0.0), (0.0, 3.4, 0.0), (-3.4, 0.0, 90.0), (3.4, 0.0, 90.0)):
        m.prop("nature_log", lx, lz, yaw)
    for i, (tx, tz, face) in enumerate(((-9.0, -8.0, "s"), (-4.0, -9.0, "s"), (4.0, -9.0, "s"), (9.0, -8.0, "s"), (-8.0, 9.0, "n"), (8.0, 9.0, "n"))):
        tent(m, tx, tz, face, [(0.3, 0.5, 0.3), (0.7, 0.4, 0.2), (0.3, 0.4, 0.7), (0.65, 0.2, 0.2)][i % 4])

    # --- The cabins: on blocks, a crawl space under every one ------------------------------
    CY = CRAWL_H + 0.1
    for i, (cx, cz) in enumerate(((-34.0, -30.0), (-22.0, -30.0), (-34.0, -16.0), (-22.0, -16.0))):
        x0, x1, z0, z1 = cx - 4.0, cx + 4.0, cz - 3.0, cz + 3.0
        for bx in (x0 + 0.3, cx, x1 - 0.3):
            for bz in (z0 + 0.3, z1 - 0.3):
                m.span(bx - 0.2, bx + 0.2, 0.0, CY - 0.1, bz - 0.2, bz + 0.2, "stone", rock)
        room_at(m, x0, z0, x1, z1, CY, "wood_floor", None, height=2.6, ceiling="wood", roof="roof", roof_tint=(0.35, 0.3, 0.26), lights=1, energy=0.8)
        m.wall((x0, z0), (x1, z0), "x", "wood", logs, height=2.6, y0=CY, thick=0.25, holes=[(2.0, 3.4, 0.9, 1.9), (5.0, 6.4, 0.9, 1.9)])
        m.wall((x0, z1), (x1, z1), "x", "wood", logs, height=2.6, y0=CY, thick=0.25,
               # A sliding door: swung into the cabin, its open panel split the room's front in
               # two, and a stand-in going round it to the far half and back lingered there for
               # ten seconds (game-hide-n-seek's headless_bots); swung out, it swept the top step.
               holes=[(3.3, 4.7, 0.0, 2.1, {"name": "cabin door", "motion": "slide", "tint": (0.4, 0.28, 0.18)})])
        m.wall((x0, z0), (x0, z1), "z", "wood", logs, height=2.6, y0=CY, thick=0.25, holes=[(2.0, 4.0, 0.9, 1.9)])
        m.wall((x1, z0), (x1, z1), "z", "wood", logs, height=2.6, y0=CY, thick=0.25, holes=[(2.0, 4.0, 0.9, 1.9)])
        # Steps half a metre wider than the door each side: as wide as the doorway, a stand-in
        # climbing them a little off the middle caught the jamb or stepped off the side, and
        # stood at the top for nine to twelve seconds (game-hide-n-seek's headless_bots).
        flight(m, cx - 1.2, cx + 1.2, z1, z1 + 2.0, 0.0, CY, "n", "wood", wood, thick=0.2)
        m.against("furniture_bed_bunk", "w", x0 + 0.15, cz - 1.2, y=CY)
        m.against("furniture_bed_bunk", "e", x1 - 0.15, cz - 1.2, y=CY)
        m.prop("furniture_table", cx, cz - 0.6, 0.0, y=CY)
        m.prop("furniture_cardboard_box_closed", x0 + 0.6, z1 - 0.6, 20.0, y=CY)

    # --- The mess hall ------------------------------------------------------------------
    hx0, hx1, hz0, hz1 = -14.0, 6.0, 18.0, 32.0
    m.room(hx0, hz0, hx1, hz1, "wood_floor", None, height=3.4, roof_tint=(0.4, 0.3, 0.26), lights=2, energy=1.0)
    m.wall((hx0, hz0), (hx1, hz0), "x", "wood", logs, height=3.4, thick=OUTER,
           holes=[(2.0, 4.0, SILL, HEAD), (8.6, 11.4, 0.0, DOOR_H, {"name": "mess hall doors", "leaves": 2, "into": "+", "tint": (0.4, 0.28, 0.18)}), (15.0, 17.0, SILL, HEAD)])
    m.wall((hx0, hz1), (hx1, hz1), "x", "wood", logs, height=3.4, thick=OUTER,
           holes=[(16.0, 17.4, 0.0, DOOR_H, {"name": "kitchen door", "into": "+", "away": False, "tint": (0.4, 0.28, 0.18)})])
    m.wall((hx0, hz0), (hx0, hz1), "z", "wood", logs, height=3.4, thick=OUTER, holes=[(4.0, 6.0, SILL, HEAD), (9.0, 11.0, SILL, HEAD)])
    m.wall((hx1, hz0), (hx1, hz1), "z", "wood", logs, height=3.4, thick=OUTER, holes=[(4.0, 6.0, SILL, HEAD)])
    m.wall((hx0, 27.0), (hx1, 27.0), "x", "wood", logs, height=3.4,
           holes=[(14.0, 15.4, 0.0, DOOR_H, {"name": "kitchen door", "into": "+", "tint": (0.4, 0.28, 0.18)})])
    for tx in (-10.0, -4.0):
        for tz in (21.0, 24.0):
            m.prop("furniture_table", tx, tz, 0.0)
            m.prop("furniture_bench", tx - 0.5, tz - 0.8, 0.0)
            m.prop("furniture_bench", tx + 0.5, tz + 0.8, 180.0)
    m.prop("furniture_kitchen_bar", 3.0, 22.0, 90.0)
    m.prop("furniture_kitchen_bar", 3.0, 23.0, 90.0)
    kitchen_row(m, hx0 + 0.2, -6.0, hz1 - 0.15, "s")
    m.prop("furniture_kitchen_fridge_large", 2.0, hz1 - 0.65, 180.0)
    m.prop("furniture_cardboard_box_closed", -4.0, 28.0, 10.0)
    # Two outhouses behind it.
    for ox in (10.0, 12.0):
        m.span(ox - 0.8, ox + 0.8, 2.4, 2.5, 30.0, 32.2, "roof", (0.35, 0.3, 0.26))
        m.wall((ox - 0.8, 32.2), (ox + 0.8, 32.2), "x", "wood", wood, height=2.4, thick=0.1)
        m.wall((ox - 0.8, 30.0), (ox - 0.8, 32.2), "z", "wood", wood, height=2.4, thick=0.1)
        m.wall((ox + 0.8, 30.0), (ox + 0.8, 32.2), "z", "wood", wood, height=2.4, thick=0.1)
        m.wall((ox - 0.8, 30.0), (ox + 0.8, 30.0), "x", "wood", wood, height=2.4, thick=0.1,
               holes=[(0.15, 1.45, 0.0, 2.1, {"name": "outhouse door", "into": "-", "away": False, "tint": (0.4, 0.5, 0.3)})])
        m.prop("furniture_toilet", ox, 31.75, 180.0)

    # --- The fire lookout: a stair tower to a cabin twelve metres up ------------------------
    lx, lz = -40.0, 18.0
    for k in range(4):
        switchback(m, lx, lz + 6.0, lz, 3.0 * k, 3.0 * (k + 1), "metal", (0.45, 0.42, 0.38), run=2.4)
        # The tower is open-sided: a rail round each landing, or the turn is a fall.
        ly = 3.0 * k + 1.5
        railing(m, lx, lz, lx + 4.0, lz, ly)
        railing(m, lx, lz, lx, lz + 3.6, ly)
        railing(m, lx + 4.0, lz, lx + 4.0, lz + 3.6, ly)
    for px in (lx - 2.0, lx + 6.0):
        for pz in (lz - 2.0, lz + 8.0):
            m.span(px - 0.15, px + 0.15, 0.0, 12.0, pz - 0.15, pz + 0.15, "wood", logs)
    for y in (3.0, 6.0, 9.0):
        m.span(lx - 2.0, lx + 6.0, y - 0.1, y, lz - 2.15, lz - 1.85, "wood", logs)
        m.span(lx - 2.0, lx + 6.0, y - 0.1, y, lz + 7.85, lz + 8.15, "wood", logs)
    slab(m, lx - 2.0, lz - 2.0, lx + 6.0, lz + 8.0, 12.0, [(lx, lz, lx + 4.0, lz + 6.0)], "wood", wood)
    for (a, b, along, holes) in (((lx - 2.0, lz - 2.0), (lx + 6.0, lz - 2.0), "x", [(1.0, 7.0, 1.0, 2.2)]),
                                 ((lx - 2.0, lz + 8.0), (lx + 6.0, lz + 8.0), "x", [(1.0, 7.0, 1.0, 2.2)]),
                                 ((lx - 2.0, lz - 2.0), (lx - 2.0, lz + 8.0), "z", [(1.0, 9.0, 1.0, 2.2)]),
                                 ((lx + 6.0, lz - 2.0), (lx + 6.0, lz + 8.0), "z", [(1.0, 9.0, 1.0, 2.2)])):
        m.wall(a, b, along, "wood", wood, height=2.6, y0=12.0, thick=0.15, holes=holes)
    m.span(lx - 2.4, lx + 6.4, 14.6, 14.85, lz - 2.4, lz + 8.4, "roof", (0.4, 0.2, 0.18))
    railing(m, lx, lz + 6.0, lx + 2.0, lz + 6.0, 12.0)
    railing(m, lx + 4.0, lz, lx + 4.0, lz + 6.0, 12.0)
    railing(m, lx, lz, lx + 4.0, lz, 12.0)
    m.prop("furniture_desk", lx + 5.0, lz + 3.0, -90.0, y=12.0)
    m.prop("furniture_chair_desk", lx + 4.3, lz + 3.0, -90.0, y=12.0)
    m.light(lx + 2.0, 14.2, lz + 3.0, rng=6.0, energy=0.6)

    # --- The treehouse and its rope bridge -----------------------------------------------
    TY = 4.0
    m.prop("nature_tree_fat", 16.0, -24.0, 20.0)
    for (a, b, c, d) in ((4.0, -28.0, 12.0, -20.0), (4.0, -42.0, 10.0, -36.0)):
        m.span(a, c, TY - 0.2, TY, b, d, "wood", wood)
        for px in (a + 0.2, c - 0.2):
            for pz in (b + 0.2, d - 0.2):
                m.span(px - 0.15, px + 0.15, 0.0, TY - 0.2, pz - 0.15, pz + 0.15, "wood", logs)
    flight(m, 4.0, 6.0, -20.0, -11.0, 0.0, TY, "n", "wood", wood, thick=0.2)
    railing(m, 6.0, -20.0, 12.0, -20.0, TY, mat="wood", tint=logs)
    railing(m, 12.0, -28.0, 12.0, -20.0, TY, mat="wood", tint=logs)
    railing(m, 4.0, -28.0, 4.0, -20.0, TY, mat="wood", tint=logs)
    railing(m, 4.0, -28.0, 6.0, -28.0, TY, mat="wood", tint=logs)
    railing(m, 7.4, -28.0, 12.0, -28.0, TY, mat="wood", tint=logs)
    m.span(7.0, 11.4, TY + 2.4, TY + 2.55, -27.2, -22.8, "roof", (0.35, 0.45, 0.3))
    m.wall((7.0, -27.0), (11.2, -27.0), "x", "wood", logs, height=2.4, y0=TY, thick=0.12)
    m.wall((7.0, -23.0), (11.2, -23.0), "x", "wood", logs, height=2.4, y0=TY, thick=0.12,
           holes=[(0.5, 1.7, 0.0, 2.0, {"name": "treehouse door", "into": "+", "away": False, "tint": (0.45, 0.6, 0.35)}), (2.4, 3.6, 1.0, 1.8)])
    m.wall((7.0, -27.0), (7.0, -23.0), "z", "wood", logs, height=2.4, y0=TY, thick=0.12, holes=[(1.4, 2.6, 1.0, 1.8)])
    m.wall((11.2, -27.0), (11.2, -23.0), "z", "wood", logs, height=2.4, y0=TY, thick=0.12)
    m.prop("furniture_cardboard_box_open", 10.4, -26.2, 10.0, y=TY)
    m.prop("furniture_pillow_blue_long", 8.0, -25.0, 0.0, y=TY)
    # The bridge, plank by plank, to the second platform, and a stair down from it.
    for i in range(10):
        m.span(6.0, 7.4, TY - 0.08, TY, -36.0 + i * 0.8, -36.0 + i * 0.8 + 0.6, "wood", wood)
    m.span(6.0, 7.4, TY - 0.12, TY - 0.08, -36.0, -28.0, "fabric", (0.5, 0.4, 0.3))
    railing(m, 6.0, -36.0, 6.0, -28.0, TY, height=1.0, mat="fabric", tint=(0.5, 0.4, 0.3))
    railing(m, 7.4, -36.0, 7.4, -28.0, TY, height=1.0, mat="fabric", tint=(0.5, 0.4, 0.3))
    railing(m, 4.0, -42.0, 10.0, -42.0, TY, mat="wood", tint=logs)
    railing(m, 4.0, -42.0, 4.0, -36.0, TY, mat="wood", tint=logs)
    railing(m, 4.0, -36.0, 6.0, -36.0, TY, mat="wood", tint=logs)
    railing(m, 7.4, -36.0, 10.0, -36.0, TY, mat="wood", tint=logs)
    flight(m, 10.0, 18.0, -40.0, -38.4, 0.0, TY, "w", "wood", wood, thick=0.2)

    # --- The lake: a dock, two boats, the boathouse ----------------------------------------
    m.span(18.0, 32.0, 0.1, 0.3, -12.0, -10.0, "wood", wood)
    for px in frange(20.0, 32.1, 3.0):
        for pz in (-11.9, -10.1):
            m.span(px - 0.1, px + 0.1, -1.2, 0.1, pz - 0.1, pz + 0.1, "wood", logs)
    lifeboat(m, 26.0, -7.6, -0.6, tint=(0.75, 0.35, 0.2))
    lifeboat(m, 30.0, -14.6, -0.6, tint=(0.25, 0.45, 0.6))
    bx0, bx1, bz0, bz1 = 10.0, 18.0, -22.0, -14.0
    m.room(bx0, bz0, bx1, bz1, "wood_floor", None, height=3.0, ceiling="wood", roof="roof", roof_tint=(0.35, 0.3, 0.26), lights=1, energy=0.7)
    m.wall((bx0, bz0), (bx1, bz0), "x", "wood", logs, height=3.0, thick=0.25, holes=[(3.0, 5.0, 1.0, 2.0)])
    m.wall((bx0, bz1), (bx1, bz1), "x", "wood", logs, height=3.0, thick=0.25)
    m.wall((bx0, bz0), (bx0, bz1), "z", "wood", logs, height=3.0, thick=0.25,
           # Out onto the shore: opened in, it stood across the 1.8 m between the wall and the
           # boat with half a metre to spare, and stand-ins leaving stood behind it for twenty
           # seconds (game-hide-n-seek's headless_bots).
           holes=[(3.3, 4.7, 0.0, DOOR_H, {"name": "boathouse door", "into": "-", "away": False, "tint": (0.4, 0.28, 0.18)})])
    # Four metres, not six: a 3 m leaf opened along the north edge of the opening, and a hider
    # fleeing through it could not shut it behind them (game-hide-n-seek's headless_bots).
    m.wall((bx1, bz0), (bx1, bz1), "z", "wood", logs, height=3.0, thick=0.25,
           holes=[(2.0, 6.0, 0.0, 2.6, {"name": "boathouse doors", "leaves": 2, "into": "+", "away": False, "angle": 88.0, "tint": (0.5, 0.36, 0.24), "seconds": 1.2})])
    lifeboat(m, 14.8, -18.0, 0.0, tint=(0.6, 0.6, 0.62))
    m.against("furniture_bookcase_open", "w", bx0 + 0.15, -20.6)
    m.prop("furniture_cardboard_box_closed", 11.0, -15.0, 15.0)

    # --- The cave in the hillside ----------------------------------------------------------
    cx0, cx1, cz0, cz1 = -56.0, -34.0, -56.0, -38.0
    CH = 2.6
    m.span(cx0, cx1, CH, CH + 1.4, cz0, cz1, "stone", rock)
    m.span(cx0 + 2.0, cx1 - 2.0, CH + 1.4, CH + 2.4, cz0 + 2.0, cz1 - 2.0, "stone", rock)
    m.span(cx0, cx1, -0.02, 0.0, cz0, cz1, "dirt", (0.4, 0.36, 0.3))
    for (a, b, along, holes) in (((cx0, cz1), (cx1, cz1), "x", [(6.0, 8.0, 0.0, 2.2)]),
                                 ((cx0, cz0), (cx1, cz0), "x", []),
                                 ((cx0, cz0), (cx0, cz1), "z", []),
                                 ((cx1, cz0), (cx1, cz1), "z", [(13.0, 14.4, 0.0, CRAWL_H)])):
        m.wall(a, b, along, "stone", rock, height=CH, thick=1.0, holes=holes, glass=False)
    crawl(m, cx1 + 0.5, -43.0, cx1 + 5.0, -41.6, "x", mat="stone", tint=rock)
    m.wall((cx0, -44.0), (cx1, -44.0), "x", "stone", rock, height=CH, thick=0.8, holes=[(10.0, 12.0, 0.0, 2.2)], glass=False)
    m.wall((-46.0, cz0), (-46.0, -44.0), "z", "stone", rock, height=CH, thick=0.8, holes=[(4.0, 6.0, 0.0, 2.2)], glass=False)
    m.wall((-40.0, -44.0), (-40.0, cz1), "z", "stone", rock, height=CH, thick=0.8, glass=False)
    m.wall((-51.0, cz0), (-51.0, -50.0), "z", "stone", rock, height=CH, thick=0.8, glass=False,
           holes=[(2.0, 3.0, 0.0, 2.0, {"name": "a rock", "secret": True, "hinge": "start"})])
    m.wall((cx0, -50.0), (-51.0, -50.0), "x", "stone", rock, height=CH, thick=0.8, glass=False)
    for (lx_, lz_) in ((-48.0, -40.0), (-38.0, -48.0), (-44.0, -52.0)):
        m.light(lx_, CH - 0.5, lz_, rng=6.0, energy=0.35, colour=(1.0, 0.7, 0.4))
    m.prop("furniture_cardboard_box_closed", -54.0, -54.0, 10.0)
    m.prop("furniture_cardboard_box_open", -53.0, -52.4, -20.0)
    m.prop("nature_mushroom_red_tall", -37.0, -54.0)
    for (rx, rz) in ((-50.0, -48.0), (-38.0, -42.0)):
        m.prop("nature_rock_small_a", rx, rz)
    for (rx, rz, kind) in ((-52.0, -46.0, "nature_rock_large_a"), (-40.0, -52.0, "nature_rock_tall_a"), (-46.0, -40.0, "nature_stone_large_a")):
        m.prop(kind, rx, rz, 30.0, y=CH + 2.4)

    # --- The archery range and the car park ---------------------------------------------
    for tz in (22.0, 28.0, 34.0):
        m.span(44.0, 44.3, 0.6, 2.0, tz - 0.7, tz + 0.7, "fabric", (0.85, 0.2, 0.2))
        m.span(44.3, 45.1, 0.0, 0.8, tz - 0.6, tz + 0.6, "sand", (0.95, 0.82, 0.45))
    m.overlay(28.0, 20.0, 46.0, 36.0, "dirt", (0.65, 0.58, 0.45))
    m.overlay(-12.0, 44.0, 12.0, 60.0, "asphalt")
    m.overlay(-2.0, 60.0, 2.0, 66.0, "asphalt")
    for i, x in enumerate((-9.0, -4.0, 4.0, 9.0)):
        car(m, x, 50.0, "z", [(0.7, 0.12, 0.1), (0.15, 0.3, 0.6), (0.85, 0.85, 0.85), (0.3, 0.5, 0.35)][i])
    m.prop("nature_sign", 0.0, 42.0, 0.0)

    for (x, z) in ((-3.0, -5.0), (3.0, -5.0), (-6.0, 3.0), (6.0, 3.0), (-2.0, 6.0), (2.0, -2.5), (-13.0, 0.0), (13.0, 0.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((-6.0, 56.0), (-2.0, 56.0), (2.0, 56.0), (6.0, 56.0)):
        m.spawn("seekers", x, z)
    return m.build()


def museum():
    """Halden Museum: two tall storeys round a rotunda with a skylight and a whale's skeleton
    hung in it, grand stairs either side of the entrance hall, and a gallery off every side.

    A museum is rooms full of things people are told not to touch, which is to say things to
    hide behind, under and inside: the dinosaur's ribs stand high enough to crouch under, the
    pyramid has a crawl tunnel through its base, the art gallery is a maze of partitions, the
    space capsule has a door. The two grand stairs and the rotunda's ring on both floors are
    the way round. The library's vault is behind a panel of its wall."""
    m = Map(
        "hs_museum", "Halden Museum",
        "Two floors of galleries round a rotunda: a dinosaur, a pyramid, a rocket, a maze of paintings, and a vault.",
        {"sky_top": [0.34, 0.5, 0.78], "sky_horizon": [0.82, 0.84, 0.88], "ground": [0.32, 0.32, 0.3],
         "sun_pitch": -52, "sun_yaw": 130, "sun_energy": 1.0, "sun_colour": [1.0, 0.96, 0.9],
         "ambient": [0.64, 0.64, 0.68], "ambient_energy": 0.52, "fog": 0.001},
        kill_y=-10.0,
    )
    X0, X1, Z0, Z1 = -30.0, 30.0, -20.0, 20.0
    H = 5.0
    TOP = 2 * H
    stone = (0.86, 0.84, 0.78)
    cream = (0.94, 0.92, 0.86)
    oak = (0.5, 0.36, 0.24)
    glass = {"mat": "glass", "tint": (1.0, 1.0, 1.0)}
    staff = {"mat": "metal", "tint": (0.45, 0.48, 0.52), "away": False}

    m.floor(-48, -40, 48, 52, "sidewalk", (0.85, 0.84, 0.8), y=GROUND)
    boundary(m, -48, -40, 48, 52, height=2.4, mat="hedge", tint=(0.75, 0.9, 0.75))

    # --- Shell ----------------------------------------------------------------------------
    atrium = (-6.0, -6.0, 6.0, 6.0)
    stairs = [(-8.0, 8.0, -6.0, 18.0), (6.0, 8.0, 8.0, 18.0)]
    m.floor(X0, Z0, X1, Z1, "parquet")
    slab(m, X0, Z0, X1, Z1, H, [atrium] + stairs, "parquet", (0.85, 0.8, 0.72))
    slab(m, X0, Z0, X1, Z1, TOP + 0.1, [atrium], "ceiling", None, thick=0.1)
    slab(m, X0 - 0.4, Z0 - 0.4, X1 + 0.4, Z1 + 0.4, TOP + 0.4, [atrium], "roof", (0.4, 0.42, 0.45), thick=0.3)
    m.span(atrium[0], atrium[2], TOP + 0.1, TOP + 0.15, atrium[1], atrium[3], "glass")
    for x in frange(-6.0, 6.1, 3.0):
        m.span(x - 0.08, x + 0.08, TOP + 0.15, TOP + 0.4, -6.0, 6.0, "metal", (0.5, 0.52, 0.55))
    for k, y in enumerate((0.0, H)):
        windows = [(x, x + 2.0, 1.2, 3.8) for x in (4.0, 12.0, 46.0, 54.0)]
        south = list(windows)
        if k == 0:
            south.append((27.2, 32.8, 0.0, 3.4, {"name": "main doors", "leaves": 2, "into": "-", "away": False, "open": True, "angle": 85.0, **glass}))
        m.wall((X0, Z1), (X1, Z1), "x", "stone", stone, height=H, y0=y, thick=OUTER, holes=south, inner="paint", inner_tint=cream)
        north = [(x, x + 2.0, 1.2, 3.8) for x in (4.0, 12.0, 46.0, 54.0)]
        if k == 0:
            north.append((32.0, 36.0, 0.0, 3.2, {"name": "loading shutter", "motion": "lift", "travel": 1.7, "mat": "metal", "tint": (0.55, 0.57, 0.6), "seconds": 1.4}))
        m.wall((X0, Z0), (X1, Z0), "x", "stone", stone, height=H, y0=y, thick=OUTER, holes=north, inner="paint", inner_tint=cream)
        m.wall((X0, Z0), (X0, Z1), "z", "stone", stone, height=H, y0=y, thick=OUTER, holes=[(8.0, 10.0, 1.2, 3.8), (30.0, 32.0, 1.2, 3.8)], inner="paint", inner_tint=cream)
        m.wall((X1, Z0), (X1, Z1), "z", "stone", stone, height=H, y0=y, thick=OUTER, holes=[(8.0, 10.0, 1.2, 3.8), (30.0, 32.0, 1.2, 3.8)], inner="paint", inner_tint=cream)
        # The wings' walls on the central hall, wide arches into each gallery.
        for wx in (-8.0, 8.0):
            m.wall((wx, Z0), (wx, Z1), "z", "paint", cream, height=H, y0=y,
                   holes=[(12.0, 16.0, 0.0, 3.6), (25.0, 29.0, 0.0, 3.6)])
        # The galleries' dividing walls.
        for zz in (2.0,):
            m.wall((X0, zz), (-8.0, zz), "x", "paint", cream, height=H, y0=y, holes=[(10.0, 13.0, 0.0, 3.6)])
            m.wall((8.0, zz), (X1, zz), "x", "paint", cream, height=H, y0=y, holes=[(9.0, 12.0, 0.0, 3.6)])
        # The north block behind the rotunda: staff rooms below, the conservation lab above.
        m.wall((-8.0, -10.0), (8.0, -10.0), "x", "paint", cream, height=H, y0=y,
               holes=[(2.0, 3.4, 0.0, DOOR_H, dict(staff, name="staff door" if k == 0 else "lab door", into="-")),
                      (12.6, 14.0, 0.0, DOOR_H, dict(staff, name="store door" if k == 0 else "lab door", into="-"))])
        if k == 0:
            m.wall((0.0, Z0), (0.0, -10.0), "z", "paint", cream, height=H, y0=y)
        for (a, b, c, d) in ((X0, Z0, -8.0, 2.0), (X0, 2.0, -8.0, Z1), (8.0, Z0, X1, 2.0), (8.0, 2.0, X1, Z1), (-8.0, Z0, 8.0, -10.0)):
            lights_over(m, a, b, c, d, y + H - 0.2, energy=1.0)
        for (lx, lz) in ((-7.0, 0.0), (7.0, 0.0), (0.0, -8.0), (0.0, 7.0), (0.0, 14.0)):
            m.light(lx, y + H - 0.6, lz, rng=9.0, energy=0.8)

    # The grand stairs, the rails round the rotunda and the stair holes.
    for (a, b, c, d) in stairs:
        flight(m, a, c, b, d, 0.0, H, "n", "stone", stone, thick=0.3)
    railing(m, -6.0, 8.0, -6.0, 18.0, H, height=1.1, mat="metal", tint=(0.7, 0.6, 0.3))
    railing(m, 6.0, 8.0, 6.0, 18.0, H, height=1.1, mat="metal", tint=(0.7, 0.6, 0.3))
    railing(m, -8.0, 18.0, -6.0, 18.0, H, height=1.1, mat="metal", tint=(0.7, 0.6, 0.3))
    railing(m, 6.0, 18.0, 8.0, 18.0, H, height=1.1, mat="metal", tint=(0.7, 0.6, 0.3))
    for (a, b, c, d) in ((-6.0, -6.0, 6.0, -6.0), (-6.0, 6.0, 6.0, 6.0), (-6.0, -6.0, -6.0, 6.0), (6.0, -6.0, 6.0, 6.0)):
        railing(m, a, b, c, d, H, height=1.1, mat="metal", tint=(0.7, 0.6, 0.3))
    # The rotunda: a globe on a plinth, and the whale hung over it.
    m.span(-1.6, 1.6, 0.0, 0.8, -1.6, 1.6, "stone", stone)
    m.span(-1.2, 1.2, 0.8, 3.0, -1.2, 1.2, "metal", (0.3, 0.45, 0.6))
    m.span(-5.0, 5.0, 6.6, 7.0, -0.25, 0.25, "plaster", (0.95, 0.93, 0.88))
    for x in frange(-3.6, 3.7, 0.9):
        m.span(x - 0.08, x + 0.08, 5.8, 7.0, -1.0, -0.85, "plaster", (0.95, 0.93, 0.88))
        m.span(x - 0.08, x + 0.08, 5.8, 7.0, 0.85, 1.0, "plaster", (0.95, 0.93, 0.88))
    m.span(4.6, 5.8, 6.4, 7.2, -0.5, 0.5, "plaster", (0.95, 0.93, 0.88))
    for (bx, bz) in ((-4.0, 10.0), (4.0, 10.0)):
        m.prop("furniture_bench_cushion", bx, bz, 0.0)
    m.prop("furniture_desk", 0.0, 12.0, 0.0)
    m.prop("furniture_chair_desk", 0.0, 11.2, 180.0)

    # --- Ground floor ---------------------------------------------------------------------
    # The dinosaur hall: a skeleton on a plinth, its ribs high enough to crouch under.
    m.span(-26.0, -12.0, 0.0, 0.3, -16.0, -6.0, "stone", stone)
    bone = (0.92, 0.88, 0.78)
    m.span(-24.0, -14.0, 3.0, 3.3, -11.2, -10.8, "plaster", bone)
    for x in frange(-22.0, -15.9, 1.0):
        m.span(x - 0.08, x + 0.08, 1.4, 3.0, -12.4, -12.25, "plaster", bone)
        m.span(x - 0.08, x + 0.08, 1.4, 3.0, -9.75, -9.6, "plaster", bone)
        m.span(x - 0.08, x + 0.08, 1.3, 1.4, -12.4, -9.6, "plaster", bone)
    for (lx_, lz_) in ((-21.5, -12.0), (-21.5, -10.0), (-16.5, -12.0), (-16.5, -10.0)):
        m.span(lx_ - 0.15, lx_ + 0.15, 0.3, 3.0, lz_ - 0.15, lz_ + 0.15, "plaster", bone)
    m.span(-27.0, -24.0, 2.6, 3.6, -11.6, -10.4, "plaster", bone)
    m.span(-14.0, -11.0, 2.6, 2.9, -11.1, -10.9, "plaster", bone)
    for (a, b, c, d) in ((-26.0, -16.0, -12.0, -16.0), (-26.0, -6.0, -12.0, -6.0), (-26.0, -16.0, -26.0, -6.0), (-12.0, -16.0, -12.0, -6.0)):
        railing(m, a, b, c, d, 0.3, height=0.6, mat="fabric", tint=(0.6, 0.15, 0.2))
    for (cx, cz) in ((-28.0, -2.0), (-24.0, -2.0), (-20.0, -2.0)):
        m.span(cx - 0.8, cx + 0.8, 0.0, 1.0, cz - 0.5, cz + 0.5, "wood", oak)
        m.span(cx - 0.75, cx + 0.75, 1.0, 1.8, cz - 0.45, cz + 0.45, "glass")
    # The cafe and the gift shop.
    for (tx, tz) in ((-27.0, 6.0), (-27.0, 10.0), (-23.0, 6.0), (-23.0, 10.0), (-27.0, 14.0)):
        m.prop("furniture_table_round", tx, tz)
        m.prop("furniture_chair_rounded", tx - 0.8, tz, 90.0)
        m.prop("furniture_chair_rounded", tx + 0.8, tz, -90.0)
    for i in range(4):
        bar = m.prop("furniture_kitchen_bar", -20.5, 13.0 + i, 90.0)
    m.on("furniture_kitchen_coffee_machine", bar)
    m.against("furniture_kitchen_fridge", "s", Z1 - 0.3, -19.5)
    m.wall((-18.0, 2.0), (-18.0, Z1), "z", "paint", cream, height=H, holes=[(6.0, 8.0, 0.0, 2.6)])
    for (sx_, sz_) in ((-15.5, 6.0), (-12.5, 6.0), (-15.5, 10.0), (-12.5, 10.0)):
        m.prop("furniture_bookcase_open", sx_, sz_, 0.0)
    for i in range(3):
        m.prop("furniture_kitchen_bar", -11.0 + 0.0, 15.0 + i, -90.0)
    m.prop("furniture_bear", -16.0, 17.0, 160.0)
    m.prop("furniture_cardboard_box_closed", -17.2, 18.6, 20.0)
    # The Egyptian room: a stepped pyramid with a crawl tunnel through its base, sarcophagi,
    # statues, and a tomb with a door.
    sand = (0.86, 0.76, 0.56)
    for i, (h0, h1) in enumerate(((0.0, 1.2), (1.2, 2.2), (2.2, 3.0), (3.0, 3.6))):
        r_ = 5.0 - i * 1.2
        if i == 0:
            for (a, b, c, d) in ((19.0 - r_, -11.0 - r_, 19.0 + r_, -11.75), (19.0 - r_, -10.25, 19.0 + r_, -11.0 + r_)):
                m.span(a, c, h0, h1, b, d, "sand", sand)
            m.span(19.0 - r_, 19.0 + r_, CRAWL_H, h1, -11.75, -10.25, "sand", sand)
        else:
            m.span(19.0 - r_, 19.0 + r_, h0, h1, -11.0 - r_, -11.0 + r_, "sand", sand)
    for (sx_, sz_) in ((11.0, -18.0), (11.0, -4.0)):
        m.span(sx_ - 1.0, sx_ + 1.0, 0.0, 0.9, sz_ - 0.5, sz_ + 0.5, "stone", (0.7, 0.6, 0.4))
        m.span(sx_ - 1.05, sx_ + 1.05, 0.9, 1.05, sz_ - 0.55, sz_ + 0.55, "stone", (0.75, 0.62, 0.3))
    for (sx_, sz_) in ((23.0, -18.6), (27.0, -4.0)):
        m.span(sx_ - 0.6, sx_ + 0.6, 0.0, 3.2, sz_ - 0.6, sz_ + 0.6, "stone", (0.7, 0.62, 0.48))
    m.wall((26.0, Z0), (26.0, -13.0), "z", "stone", (0.7, 0.62, 0.48), height=3.0,
           holes=[(2.6, 4.0, 0.0, 2.2, {"name": "tomb door", "into": "+", "away": False, "mat": "stone", "tint": (0.6, 0.52, 0.4)})])
    m.wall((26.0, -13.0), (X1, -13.0), "x", "stone", (0.7, 0.62, 0.48), height=3.0)
    m.span(26.0, X1 - 0.15, 3.0, 3.15, Z0 + 0.15, -13.0, "stone", (0.7, 0.62, 0.48))
    m.span(27.6, 29.6, 0.0, 0.9, -19.6, -18.6, "stone", (0.75, 0.62, 0.3))
    m.light(28.0, 2.6, -16.0, rng=4.0, energy=0.3, colour=(1.0, 0.7, 0.4))
    # The art gallery: free-standing partitions, a painting on each side.
    for i, (a, b, along) in enumerate((((12.0, 6.0), (12.0, 12.0), "z"), ((16.0, 9.0), (22.0, 9.0), "x"), ((20.0, 13.0), (20.0, 18.5), "z"),
                                       ((24.0, 5.0), (24.0, 11.0), "z"), ((14.0, 15.0), (18.0, 15.0), "x"), ((26.0, 14.0), (29.5, 14.0), "x"))):
        m.wall(a, b, along, "paint", (0.96, 0.95, 0.92), height=3.2, thick=0.3)
        pc = [(0.7, 0.2, 0.2), (0.2, 0.4, 0.7), (0.85, 0.7, 0.2), (0.25, 0.55, 0.35)][i % 4]
        if along == "z":
            mid = (a[1] + b[1]) / 2
            m.span(a[0] - 0.18, a[0] - 0.15, 1.2, 2.6, mid - 1.0, mid + 1.0, "flat", pc)
            m.span(a[0] + 0.15, a[0] + 0.18, 1.2, 2.6, mid - 1.0, mid + 1.0, "flat", pc)
        else:
            mid = (a[0] + b[0]) / 2
            m.span(mid - 1.0, mid + 1.0, 1.2, 2.6, a[1] - 0.18, a[1] - 0.15, "flat", pc)
            m.span(mid - 1.0, mid + 1.0, 1.2, 2.6, a[1] + 0.15, a[1] + 0.18, "flat", pc)
    m.prop("furniture_bench_cushion", 16.0, 6.0, 0.0)
    m.prop("furniture_bench_cushion", 26.0, 18.6, 180.0)
    # The staff rooms: security office (monitors, lockers) and the loading store.
    d_ = m.prop("furniture_desk", -4.0, -18.8, 0.0)
    m.on("furniture_computer_screen", d_, dx=-0.4)
    m.on("furniture_computer_screen", d_, dx=0.4)
    m.prop("furniture_chair_desk", -4.0, -17.8, 180.0)
    m.span(-7.85, -7.4, 0.0, 2.0, -19.6, -14.0, "locker")
    m.prop("furniture_coat_rack_standing", -1.0, -11.0)
    for (bx, bz) in ((3.0, -18.6), (3.6, -18.2), (5.0, -18.8), (6.6, -17.0), (2.4, -15.0), (6.0, -12.0)):
        m.span(bx - 0.5, bx + 0.5, 0.0, 1.0, bz - 0.5, bz + 0.5, "wood", (0.66, 0.5, 0.34))
    m.span(2.9, 3.7, 1.0, 1.8, -18.9, -18.1, "wood", (0.66, 0.5, 0.34))

    # --- Upper floor ----------------------------------------------------------------------
    U = H
    # The armour hall: display cases on plinths, suits on stands, banners.
    for (cx, cz) in ((-26.0, -16.0), (-20.0, -16.0), (-14.0, -16.0), (-26.0, -9.0), (-14.0, -9.0), (-20.0, -3.0)):
        m.span(cx - 1.0, cx + 1.0, U, U + 1.0, cz - 0.6, cz + 0.6, "wood", oak)
        m.span(cx - 0.95, cx + 0.95, U + 1.0, U + 2.0, cz - 0.55, cz + 0.55, "glass")
    for (sx_, sz_) in ((-20.0, -9.5), (-17.0, -12.0), (-23.0, -12.0), (-11.0, -1.0)):
        m.prop("furniture_coat_rack_standing", sx_, sz_, y=U)
    for (bx, bz) in ((-29.7, -14.0), (-29.7, -6.0)):
        m.span(bx - 0.03, bx + 0.03, U + 1.4, U + 4.0, bz - 0.8, bz + 0.8, "fabric", (0.6, 0.12, 0.15))
    # The library, and its vault behind a panel.
    for bz in (6.0, 9.0, 12.0):
        for bx in (-22.0, -19.0, -16.0, -13.0):
            m.prop("furniture_bookcase_closed_wide" if (int(bx) + int(bz)) % 2 else "furniture_bookcase_open", bx, bz, 0.0, y=U)
    m.prop("furniture_table", -20.0, 16.6, 0.0, y=U)
    m.prop("furniture_chair", -20.6, 17.4, 180.0, y=U)
    m.prop("furniture_chair", -19.4, 15.8, 0.0, y=U)
    m.wall((-25.0, 14.0), (-25.0, Z1), "z", "paint", cream, height=H, y0=U,
           holes=[(2.0, 3.0, 0.0, 2.1, {"name": "panel", "secret": True, "hinge": "start"})])
    m.wall((X0, 14.0), (-25.0, 14.0), "x", "paint", cream, height=H, y0=U)
    m.against("furniture_bookcase_closed", "e", -25.1, 15.0, y=U)
    m.against("furniture_bookcase_closed", "e", -25.1, 18.2, y=U)
    m.span(-29.6, -28.4, U, U + 1.6, 14.4, 15.6, "metal", (0.3, 0.32, 0.34))
    m.prop("furniture_cardboard_box_closed", -27.0, 18.8, 30.0, y=U)
    m.prop("furniture_cardboard_box_closed", -28.6, 19.0, 5.0, y=U)
    m.light(-27.5, U + 2.6, 17.0, rng=4.0, energy=0.35, colour=(1.0, 0.8, 0.55))
    # The space gallery: a rocket, a capsule with a door, a moon rock under glass.
    for i, (h0, h1, tint) in enumerate(((0.0, 1.4, (0.9, 0.9, 0.92)), (1.4, 3.0, (0.9, 0.9, 0.92)), (3.0, 4.0, (0.8, 0.2, 0.2)), (4.0, 4.6, (0.3, 0.3, 0.32)))):
        r_ = (0.9, 0.8, 0.6, 0.25)[i]
        m.span(24.0 - r_, 24.0 + r_, U + h0, U + h1, -14.0 - r_, -14.0 + r_, "metal", tint)
    for (fx, fz) in ((22.6, -15.4), (25.4, -15.4), (22.6, -12.6), (25.4, -12.6)):
        m.span(fx - 0.2, fx + 0.2, U, U + 1.0, fz - 0.2, fz + 0.2, "metal", (0.8, 0.2, 0.2))
    cpx0, cpx1, cpz0, cpz1 = 12.0, 15.0, -18.0, -15.0
    m.span(cpx0, cpx1, U + 2.2, U + 2.4, cpz0, cpz1, "metal", (0.75, 0.76, 0.78))
    m.wall((cpx0, cpz0), (cpx1, cpz0), "x", "metal", (0.75, 0.76, 0.78), height=2.2, y0=U, thick=0.15)
    m.wall((cpx0, cpz0), (cpx0, cpz1), "z", "metal", (0.75, 0.76, 0.78), height=2.2, y0=U, thick=0.15, holes=[(1.0, 2.0, 1.2, 1.7)])
    m.wall((cpx1, cpz0), (cpx1, cpz1), "z", "metal", (0.75, 0.76, 0.78), height=2.2, y0=U, thick=0.15)
    m.wall((cpx0, cpz1), (cpx1, cpz1), "x", "metal", (0.75, 0.76, 0.78), height=2.2, y0=U, thick=0.15,
           holes=[(0.9, 2.1, 0.0, 1.9, {"name": "hatch", "into": "+", "away": False, "mat": "metal", "tint": (0.6, 0.62, 0.65)})])
    m.span(16.0, 17.6, U, U + 1.0, -6.0, -4.4, "wood", oak)
    m.span(16.05, 17.55, U + 1.0, U + 1.8, -5.95, -4.45, "glass")
    # The textiles gallery: stands of clothes in rows, and a fitting room.
    for rz in (6.0, 10.0, 14.0):
        for rx in (12.0, 15.0, 18.0, 21.0, 24.0):
            m.prop("furniture_coat_rack_standing", rx, rz, (rx * 13) % 90, y=U)
    fitting_rooms(m, 26.6, Z1 - 0.2, 1, U, booths=2)
    # The conservation lab over the staff rooms.
    for (tx, tz) in ((-4.0, -16.0), (2.0, -16.0), (-4.0, -12.6), (2.0, -12.6)):
        t = m.prop("furniture_table", tx, tz, 0.0, y=U)
        m.on("furniture_lamp_round_table", t, dx=0.5)
    m.against("furniture_bookcase_closed_doors", "n", Z0 + 0.15, 5.6, y=U)

    # --- Outside: the plaza, statues, a fountain, the loading dock -----------------------------
    m.overlay(-20.0, 20.5, 20.0, 40.0, "tile", (0.8, 0.78, 0.74))
    for (sx_, sz_) in ((-12.0, 26.0), (12.0, 26.0)):
        m.span(sx_ - 1.0, sx_ + 1.0, 0.0, 1.2, sz_ - 1.0, sz_ + 1.0, "stone", stone)
        m.span(sx_ - 0.4, sx_ + 0.4, 1.2, 3.4, sz_ - 0.4, sz_ + 0.4, "stone", (0.6, 0.62, 0.6))
    for (a, b, c, d) in ((-3.0, 30.0, 3.0, 30.4), (-3.0, 35.6, 3.0, 36.0), (-3.0, 30.4, -2.6, 35.6), (2.6, 30.4, 3.0, 35.6)):
        m.span(a, c, 0.0, 0.6, b, d, "stone", stone)
    m.span(-2.6, 2.6, 0.4, 0.45, 30.4, 35.6, "water", None, solid=False)
    for (bx, bz, yaw) in ((-8.0, 33.0, 90.0), (8.0, 33.0, -90.0), (-16.0, 36.0, 0.0), (16.0, 36.0, 0.0)):
        m.prop("furniture_bench", bx, bz, yaw)
    for (tx, tz, kind) in ((-24.0, 30.0, "nature_tree_default"), (24.0, 30.0, "nature_tree_oak"), (-40.0, -30.0, "nature_tree_tall"),
                           (40.0, -30.0, "nature_tree_fat"), (-40.0, 44.0, "nature_tree_detailed"), (40.0, 44.0, "nature_tree_default")):
        m.prop(kind, tx, tz, (tx * 7) % 90)
    container(m, -2.0, -34.0, "x", open_end="w", tint=(0.3, 0.4, 0.55))
    m.span(-6.0, 6.0, 0.0, 1.2, -24.0, -21.0, "concrete")

    for (x, z) in ((-4.0, 9.0), (4.0, 9.0), (-7.0, -4.0), (7.0, 4.0), (-4.0, -8.0), (4.0, -8.0), (0.0, 15.0), (-3.0, 4.0)):
        m.spawn("hiders", x, z)
    for (x, z) in ((-6.0, 32.0), (-10.0, 32.0), (6.0, 38.0), (10.0, 38.0)):
        m.spawn("seekers", x, z)
    return m.build()


# --- Toys -------------------------------------------------------------------------------
#
# dot-props' stock toys, by id: (width along the toy's x, depth along its z, height, loose).
# A loose toy (a ball, a barrel) is pushed by whoever walks into it and needs only its own
# room; one that stands (a swing, a see-saw) wants room round it to walk past. The numbers are
# dot-props' DotPropToyCatalogue sizes with the frames counted in (a punching bag's post, a
# swing's sweep).
TOYS = {
    "beach_ball": (1.0, 1.0, 1.0, True),
    "football": (0.24, 0.24, 0.24, True),
    "bowling_ball": (0.22, 0.22, 0.22, True),
    "bowling_pin": (0.12, 0.12, 0.38, True),
    "barrel": (0.6, 0.6, 0.85, True),
    "tyre": (0.28, 0.8, 0.8, True),
    "punching_bag": (2.0, 1.2, 2.7, False),
    "swing": (2.4, 2.8, 2.4, False),
    "seesaw": (3.2, 0.6, 0.9, False),
    "merry_go_round": (2.8, 2.8, 1.3, False),
    "wobbly_platform": (1.6, 1.6, 0.6, False),
    "trampoline": (2.4, 2.4, 0.5, False),
    "bounce_pad": (1.2, 1.2, 0.2, False),
    "light_switch": (0.3, 0.3, 1.1, False),
    "lamp_post": (0.4, 0.4, 2.8, False),
    "fountain": (1.8, 1.8, 1.1, False),
    "horn": (0.5, 0.6, 1.5, False),
    "bell": (1.4, 0.4, 2.3, False),
}

# The room a launcher's flight needs clear, instead of its footprint: a trampoline throws a
# person 2.3 m up wherever they were heading, and a pad 5 m along its arrow (+z) and back down,
# so neither may stand where the flight would land them on a roof or a wall top that was meant
# to be reached another way (or not at all).
TOY_CLEAR = {
    "trampoline": (7.0, 7.0),
    "bounce_pad": (2.4, 14.0),
}

def bowling(m, x, z, yaw=0.0, y=0.0, lane=4.0, near=10.0):
    """Ten pins in a triangle and a ball `lane` metres in front of the head pin, along the
    toy's -z turned by yaw (the way a bowler faces them), settled as one set."""
    group = "bowling%d" % len(m.toys)
    a = math.radians(yaw)
    def at(dx, dz):
        return x + dx * math.cos(a) + dz * math.sin(a), z - dx * math.sin(a) + dz * math.cos(a)
    for row in range(4):
        for k in range(row + 1):
            px, pz = at((k - row / 2.0) * 0.32, -row * 0.28)
            m.toy("bowling_pin", px, pz, y=y, near=near, group=group)
    bx, bz = at(0.0, lane)
    m.toy("bowling_ball", bx, bz, y=y, near=near, group=group)


def lamps_on_a_switch(m, lamps, switch, y=0.0):
    """Lamp posts and the switch that turns them all on and off together."""
    ids = [m.toy("lamp_post", lx, lz, y=y, near=3.0) for (lx, lz) in lamps]
    m.toy("light_switch", switch[0], switch[1], y=y, links=ids, near=3.0)


def toys_park(m):
    """A playground's: a swing, a see-saw, a roundabout and wobbly platforms by the towers, a
    trampoline and a bounce pad on the field, balls about, a fountain by the picnic shelter,
    and the west path's lamps on a switch."""
    m.toy("swing", -17.0, 6.0, 90.0, near=8.0)
    m.toy("seesaw", -8.0, 10.0, near=6.0)
    m.toy("merry_go_round", 10.0, -9.5, near=8.0)
    m.toy("wobbly_platform", 3.0, 11.0, near=6.0)
    m.toy("wobbly_platform", 5.5, 11.5, near=6.0)
    m.toy("trampoline", -30.0, -8.0, near=10.0)
    m.toy("bounce_pad", 0.0, 22.0, near=10.0)
    m.toy("beach_ball", 0.0, 13.0, near=6.0)
    m.toy("beach_ball", 20.0, 12.0, near=6.0)
    m.toy("football", -30.0, -27.0, near=6.0)
    m.toy("tyre", -2.0, 1.5, 30.0, near=4.0)
    m.toy("tyre", 3.0, -0.5, -20.0, near=4.0)
    m.toy("fountain", 24.0, 5.0, near=6.0)
    lamps_on_a_switch(m, [(-22.0, 2.6), (-34.0, 2.6)], (-28.0, 2.4))


def toys_camp(m):
    """A summer camp's: the dinner bell by the mess hall and the wake-up horn, a football by the
    campfire, beach balls on the lake shore, an obstacle course of tyres and wobbly platforms, a
    tyre swing, a punching bag and a trampoline, and barrels by the kitchen."""
    m.toy("bell", -4.0, 15.5, near=6.0)
    m.toy("horn", 9.0, 15.0, near=8.0)
    m.toy("football", 0.0, 10.0, near=6.0)
    m.toy("beach_ball", 16.0, -6.0, near=8.0)
    m.toy("beach_ball", 14.0, -14.0, near=8.0)
    for i in range(4):
        m.toy("tyre", 20.0 + i * 1.2, 6.0 + (i % 2) * 0.6, 90.0, near=6.0)
    m.toy("wobbly_platform", 22.0, 10.0, near=8.0)
    m.toy("wobbly_platform", 24.5, 11.0, near=8.0)
    m.toy("swing", 8.0, -26.0, near=10.0)
    m.toy("punching_bag", -20.0, 10.0, near=8.0)
    m.toy("trampoline", -24.0, -22.0, near=10.0)
    m.toy("barrel", 8.0, 21.0, near=6.0)
    m.toy("barrel", 8.0, 22.0, near=6.0)


def toys_castle(m):
    """A castle's: the bell before the keep, the herald's horn by the gate, a punching bag in
    the training yard, barrels by the smithy and the stables, and skittles on the courtyard."""
    m.toy("bell", 4.0, -5.0, near=6.0)
    m.toy("horn", 4.0, 26.0, near=6.0)
    m.toy("punching_bag", -2.0, 20.0, near=8.0)
    m.toy("barrel", 12.5, -3.0, near=5.0)
    m.toy("barrel", 13.4, -3.6, near=5.0)
    m.toy("barrel", -4.5, 14.0, near=5.0)
    bowling(m, 18.0, 16.0, 90.0)


def toys_mall(m):
    """A shopping centre's: beach balls and a football loose from the sports shop, wobbly
    platforms in a play corner off the atrium, a roundabout and a trampoline at the car park's
    play area, its lamps on a switch, and a bounce pad on the loading dock."""
    m.toy("beach_ball", 8.0, -4.0, near=6.0)
    m.toy("beach_ball", -8.0, 4.0, near=6.0)
    m.toy("football", 10.0, 6.0, near=6.0)
    m.toy("wobbly_platform", -10.0, -8.0, near=8.0)
    m.toy("wobbly_platform", -12.5, -8.5, near=8.0)
    m.toy("merry_go_round", -30.0, 30.0, near=12.0)
    m.toy("trampoline", 30.0, 32.0, near=12.0)
    m.toy("bounce_pad", 0.0, -30.0, 90.0, near=12.0)
    lamps_on_a_switch(m, [(-10.0, 22.0), (10.0, 22.0)], (0.0, 22.0))


def toys_manor(m):
    """A country house's: a swing and croquet-lawn skittles in the garden, a see-saw by the
    gazebo, a garden bell at the greenhouse, a football on the lawn, and the drive's lamps on a
    switch by the door."""
    m.toy("swing", 26.0, 14.0, near=10.0)
    bowling(m, 14.0, 20.0, 180.0)
    m.toy("seesaw", 26.0, -1.0, near=8.0)
    m.toy("bell", 18.0, -10.0, near=8.0)
    m.toy("football", -12.0, 14.0, near=8.0)
    m.toy("beach_ball", 8.0, 22.0, near=8.0)
    lamps_on_a_switch(m, [(-5.0, 16.0), (5.0, 16.0)], (0.0, 10.0))


def toys_museum(m):
    """A museum's interactive corners: the bell and the horn of the sound exhibit, a wobbly
    platform for the balance exhibit, the plaza's lamps on a switch and its children's
    trampoline, and a beach ball from the gift shop."""
    m.toy("bell", -6.0, 26.0, near=8.0)
    m.toy("horn", 6.0, 26.0, near=8.0)
    m.toy("wobbly_platform", 0.0, 34.0, near=8.0)
    m.toy("trampoline", -26.0, 32.0, near=12.0)
    m.toy("beach_ball", -18.0, 16.0, near=8.0)
    m.toy("football", 14.0, 34.0, near=8.0)
    lamps_on_a_switch(m, [(-12.0, 22.0), (12.0, 22.0)], (9.0, 22.0))


def toys_school(m):
    """A school's: the bell in the playground, footballs on the field, a beach ball, a swing,
    a see-saw and a roundabout on the playground, tyres for the obstacle course and a trampoline
    by the field, and a punching bag in the gym."""
    m.toy("bell", 4.0, -20.0, near=8.0)
    m.toy("football", 0.0, 30.0, near=8.0)
    m.toy("football", 6.0, 34.0, near=8.0)
    m.toy("beach_ball", -20.0, -22.0, near=8.0)
    m.toy("swing", -30.0, -30.0, near=10.0)
    m.toy("seesaw", 6.0, -24.0, near=8.0)
    m.toy("merry_go_round", -26.0, -20.0, near=10.0)
    for i in range(3):
        m.toy("tyre", 30.0 + i * 1.3, 20.0, 90.0, near=8.0)
    m.toy("punching_bag", 20.0, 0.0, near=6.0)
    m.toy("trampoline", -32.0, 30.0, near=10.0)


def toys_ship(m):
    """A liner's: the ship's bell and horn on the forward deck, barrels and tyre fenders aft and
    on the quay, beach balls by the pool on the sun deck, and the quay's lamps on a switch."""
    M, S, Q = 3.2, 6.4, 2.0
    m.toy("bell", 40.0, -4.0, y=M, near=6.0)
    m.toy("horn", 40.0, 4.5, y=M, near=6.0)
    m.toy("barrel", -38.0, -3.0, y=M, near=4.0)
    m.toy("barrel", -38.0, 3.0, y=M, near=4.0)
    m.toy("beach_ball", -5.0, 4.0, y=S, near=6.0)
    m.toy("beach_ball", 5.0, -6.0, y=S, near=6.0)
    for (x, z) in ((-20.0, 22.0), (-18.8, 22.4), (20.0, 22.0)):
        m.toy("barrel", x, z, y=Q, near=6.0)
    m.toy("tyre", -30.0, 16.0, y=Q, near=6.0)
    m.toy("tyre", 30.0, 16.0, y=Q, near=6.0)
    lamps_on_a_switch(m, [(-14.0, 28.0), (14.0, 28.0)], (0.0, 30.0), y=Q)


def toys_village(m):
    """A village's: the fountain in the square (its stone one is a basin; this one runs), the
    hall's bell, a swing and a see-saw on the green, barrels and tyres by the barn, a football
    and the square's lamps on a switch."""
    m.toy("fountain", 6.0, 6.0, near=6.0)
    m.toy("bell", 10.0, 23.0, near=8.0)
    m.toy("swing", -14.0, 34.0, near=10.0)
    m.toy("seesaw", 16.0, 30.0, near=10.0)
    m.toy("barrel", 18.0, 9.0, near=6.0)
    m.toy("barrel", 18.8, 9.6, near=6.0)
    m.toy("tyre", 17.0, -9.0, 90.0, near=6.0)
    m.toy("football", -6.0, 8.0, near=6.0)
    m.toy("beach_ball", 2.0, -14.0, near=8.0)
    lamps_on_a_switch(m, [(-9.0, -9.0), (9.0, -9.0)], (0.0, -10.0))


def toys_warehouse(m):
    """A warehouse's: barrels and tyres in the yard and the hall, the forklift's horn, a punching
    bag and a football where the shift takes its break, and the yard's lamps on a switch."""
    for (x, z) in ((-22.0, 14.0), (-21.2, 14.6), (-20.4, 14.0), (8.0, 14.0), (-10.0, 6.0)):
        m.toy("barrel", x, z, near=6.0)
    for (x, z) in ((12.0, 20.0), (13.3, 20.0), (-28.0, -20.0)):
        m.toy("tyre", x, z, 90.0, near=6.0)
    m.toy("horn", 4.0, 8.0, near=6.0)
    m.toy("punching_bag", 22.0, 6.0, near=8.0)
    m.toy("football", 0.0, 20.0, near=8.0)
    m.toy("bounce_pad", -30.0, 20.0, 90.0, near=10.0)
    lamps_on_a_switch(m, [(-6.0, 24.0), (6.0, 24.0)], (0.0, 24.0))


def toys_apartments(m):
    """A block of flats': the back yard's swing, see-saw and trampoline, a football and a beach
    ball, tyres in the alley, and the yard's lamps on a switch by the back door."""
    m.toy("swing", -8.0, -22.0, near=8.0)
    m.toy("seesaw", 2.0, -22.0, near=8.0)
    m.toy("trampoline", -12.0, -18.0, near=8.0)
    m.toy("football", 0.0, -17.0, near=6.0)
    m.toy("beach_ball", 5.0, -19.0, near=6.0)
    m.toy("tyre", -24.0, -8.0, 90.0, near=6.0)
    m.toy("tyre", -24.0, -4.0, 90.0, near=6.0)
    lamps_on_a_switch(m, [(-14.0, -24.0), (6.0, -24.0)], (-4.0, -15.0))


TOY_PLANS = {
    "hs_park": toys_park,
    "hs_camp": toys_camp,
    "hs_castle": toys_castle,
    "hs_mall": toys_mall,
    "hs_manor": toys_manor,
    "hs_museum": toys_museum,
    "hs_school": toys_school,
    "hs_ship": toys_ship,
    "hs_village": toys_village,
    "hs_warehouse": toys_warehouse,
    "hs_apartments": toys_apartments,
}


MAPS = {
    "hs_apartments": apartments,
    "hs_warehouse": warehouse,
    "hs_manor": manor,
    "hs_village": village,
    "hs_park": park,
    "hs_school": school,
    "hs_mall": mall,
    "hs_ship": ship,
    "hs_castle": castle,
    "hs_camp": camp,
    "hs_museum": museum,
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
        print("%-14s %5d boxes %5d props %3d lights %3d doors  %s" % (mid, len(doc["boxes"]), len(doc["props"]), len(doc["lights"]), len(doc["doors"]), doc["name"]))
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
    # Where a thing set down on a prop rests, from the game's part-shaped collision.
    if os.path.exists(GAME_PIECES):
        with open(GAME_PIECES) as f:
            tops = json.load(f).get("tops", {})
        for pid, grid in tops.items():
            if pid in sizes:
                sizes[pid]["tops"] = grid
    with open(SIZES_FILE, "w") as f:
        json.dump({"comment": "Prop sizes from game-hide-n-seek's props/catalogue.json, and where a thing set down on a prop rests (tops, from its props/pieces.json), copied by tools/build_maps.py --sync-sizes. The builder stands props against walls and on each other with these; the game measures the models, this is a copy.", "props": sizes}, f, indent=1, sort_keys=True)
        f.write("\n")
    print("copied %d prop sizes" % len(sizes))


if __name__ == "__main__":
    if "--spots" in sys.argv:
        at = sys.argv.index("--spots")
        mid = sys.argv[at + 1]
        y = float(sys.argv[at + 2]) if len(sys.argv) > at + 2 else 0.0
        # The map without its toys, so the spots are the ones a toy could take.
        TOY_PLANS.pop(mid, None)
        holder = {}
        real_build = Map.build
        def capture(self):
            holder["m"] = self
            return real_build(self)
        Map.build = capture
        MAPS[mid]()
        m = holder["m"]
        # A plan of the floor at y, a character a metre: '@' 4 m clear round it, 'o' 2.5 m,
        # '.' 1.2 m, ',' floor with less, ' ' no floor; 'S' a spawn, 'D' a door. North (-z) up.
        xs = [b["at"][0] for b in m.boxes]
        zs = [b["at"][2] for b in m.boxes]
        x0, x1, z0, z1 = int(min(xs)), int(max(xs)) + 1, int(min(zs)), int(max(zs)) + 1
        print("x from %d to %d, z from %d (top) to %d; a tick every 10 m" % (x0, x1, z0, z1))
        print("     " + "".join("|" if x % 10 == 0 else " " for x in range(x0, x1)))
        spawn_at = {(round(s_["at"][0]), round(s_["at"][2])) for side in m.spawns.values() for s_ in side if abs(s_["at"][1] - y) < 1.0}
        door_at = {(round(d["at"][0]), round(d["at"][2])) for d in m.doors if abs(d["at"][1] - y - 1.15) < 1.5}
        for z in range(z0, z1):
            row = []
            for x in range(x0, x1):
                if (x, z) in spawn_at:
                    row.append("S")
                    continue
                if (x, z) in door_at:
                    row.append("D")
                    continue
                if not m._floored(x + 0.0, y, z + 0.0, edge=0.0):
                    row.append(" ")
                    continue
                ch = ","
                for size, c in ((4.0, "@"), (2.5, "o"), (1.2, ".")):
                    TOYS["_probe"] = (size, size, 2.0, True)
                    if m._toy_clear({"toy": "_probe", "at": [x, y, z]}, x, y, z, []) is None:
                        ch = c
                        break
                row.append(ch)
            TOYS.pop("_probe", None)
            print("%4d " % z + "".join(row) + ("  -" if z % 10 == 0 else ""))
        sys.exit(0)
    if "--sync-sizes" in sys.argv:
        sync_sizes()
        sys.exit(0)
    sys.exit(write_all("--check" in sys.argv))
