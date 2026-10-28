# hide-n-seek-maps

The maps game-hide-n-seek plays, as JSON documents. Read `../../CLAUDE.md` and `../game-hide-n-seek/CLAUDE.md` first: the format is defined and validated by game-hide-n-seek's `game/hs_map_doc.gd`, and `maps/README.md` is the mapper's copy of it.

**`maps/` is what the game reads**, through a dot-bootstrap link (`game-hide-n-seek/.gitignore` names it: `# bootstrap-link: hide-n-seek-maps/maps`). The game reads every `.json` there plus the practice house built into it; a refused document is listed by `hs_maps` with the reason, and every other map still plays.

**`tools/build_maps.py` writes every document, and the JSON is committed.** Edit the builder, run it, commit both. `--check` (CI) fails when a committed file differs from what the builder writes. The library half (walls, rooms, `against`, spawn settling and facing) came from mg-prop-hunt-maps' builder on 2026-10-10; what is this repository's own is the hide-and-seek half below it.

**Prop sizes are a snapshot** (`tools/prop_sizes.json`), copied from game-hide-n-seek's `props/catalogue.json` by `--sync-sizes`, so the builder (and CI) runs without the game checked out. **`on()` rests a prop on what is under its footprint**, from the support's `tops` grid (copied from the game's `props/pieces.json`, its part-shaped collision): a bed's mattress, not its headboard's height. Re-run `--sync-sizes` after the game re-measures its props.

**`tools/import_bsp.py` cuts a BSP brush into boxes with dot-map's `boxes.decompose` (never a local copy; it is the adapter: document format, texture and prop tables, per-map flags).** The flags a committed imported map was made with are not recorded, so a re-import does not reproduce its document byte for byte (and neither does the old importer). The check that does hold: a brush's boxes against the previous local cutter, which differ only where dot-map is the more correct (slabs along the slope, a thin prism as slabs rather than a rectangle twice its size).

## What a hide-and-seek map needs, and the helpers that make it

A map is judged by three things the genre's best maps share: **more than one way round** (a chase into a dead end is over), **somewhere up** (a roof, a loft, a gallery, a rack), and **somewhere a seeker has to crouch to look** (a crawl space, a rack's bottom shelf, the space under a bridge). Every map here has all three, and the builder has one call for each part:

- `flight(m, x0, x1, z0, z1, y0, y1, ascend)`: a straight flight. **Steps are slabs 0.3 m deep, not blocks to the floor**, because a switchback stairwell's upper flight passes over the lower one: a solid block left 1.7 m of headroom at the turn, under a person's height. 0.22 m a step (a player steps 0.4).
- `crawl(...)`: a tunnel 1.1 m high, open at both ends. A person crouched is 0.95 m, standing 1.8 m. `headless_climb` tries one standing (refused) and crouched (through).
- `rack(...)`: warehouse shelving whose first shelf's underside is 1.27 m over a 0.15 m deck, so every bottom bay is a crawl space, with boxes in some bays and not others.
- `container(...)`: a shipping container, walkable on top, optionally open at one end, where it has a pair of doors that swing outward only (`doors="shut"`, `"open"` or None).
- `hedge_maze(...)`: a seeded depth-first maze, the same every build (`random.Random(seed)`), gates on two sides.
- `room_at(...)`: `Map.room` for an upper storey or a mezzanine.
- `slab(...)`: a floor with rectangular holes left in it (an upper storey over stairwells, an atrium), as a few strips of boxes.
- `classroom(...)`, `shop(...)` (six kinds), `fitting_rooms(...)`, `lights_over(...)`: the school's and the mall's rooms.
- `switchback(...)` one storey of a switchback stair (stack one per storey), `lifeboat(...)`, `crenellate(...)` (merlons along a wall top), `tent(...)` (a box cannot pitch, so its roof steps in to a ridge).
- **A stair whose top or turn is open to the air wants a landing with a rail** (the castle's wall stairs, the camp's lookout): `headless_climb`'s runner reached the top of each and ran straight off it, as a player holding forward would.
- **A door in a wall thicker than 0.45 m hangs on the face it opens towards, square, one way** (`Map.door`): in the middle of a castle's 2 m opening it swung into the opening's own sides.
- **Every furnishing helper takes `y`** (`stall` did not either: the ship's sun-deck kiosk was first built in a cabin doorway, and the ticket office was a `room` whose ceiling stood a metre over the quay, through its door; anything above y 0 wants `room_at`): `bathroom` and `lockers` did not, and the school's upstairs toilets were first built on top of the downstairs ones, two stall doors in every doorway. `headless_maps`' doorway check found it.

## Doors, and why a door is a hole's fifth element

The brief asked for maps as interactive as the genre's best, and what those have that a building of boxes does not is things that open: a door you shut behind you, a cupboard, a stall, a gate, a shutter, and a wall that turns out not to be one. Every map has doors (game-hide-n-seek's `HsDoors` is the runtime; `maps/README.md` the format).

**A door is declared on the hole it fills**: `(3.0, 4.4, 0.0, DOOR_H, {"name": "bedroom door"})`, and `Map.wall` hangs it. A door placed by coordinates beside the wall call is a door one edit away from not matching its hole, and nothing but a render would say so. `{"leaves": 2}` hangs a pair; a hole with a door gets no glass, which a raised hole otherwise does (the hayloft's doors were first built behind a pane of it). `fence(..., gate={...})` hangs one in every gap and `bathroom(..., doors=True)` one on every stall.

**A swinging door opens away from whoever opens it** unless `away` is False, and `into` is only the way it stands when the map has it open. So `away: False` is for a door with something on one side: a stall (the toilet), a cottage's back door (the stove), a fire door (outward only, onto the escape), a door on a narrow walkway. **A secret door** slides into its wall, is the wall's own material and nearly its thickness, has no handle, and has a 6 mm gap where a door has 3 cm: with 3 cm its outline was the first thing a render showed. There is one on every map but the warehouse: a loose panel between two top-floor bedrooms in the flats, a cupboard under the manor's stair, a hollow stage in the village hall, loose tiles between the park toilets' two sides, the school library's archive, the mall cinema's projection booth, the ship's steering-gear room, the castle's escape tunnel, the camp cave's hidden chamber, the museum library's vault.

`headless_maps` swings every door each way it can go and fails one that passes through a wall or a prop, and sends a ray through every doorway with its door open.

**An open panel must leave a body's width (0.8 m) round its free edge, and a door pinned to open one way wants 2 m of floor clear of its swing on that side** to be opened from: game-hide-n-seek's stand-ins walk round a panel's free edge and step out of a swing before using a door, and `headless_bots` found the places where neither could happen (a back door spanning the strip to a yard wall, a court gate beside a bench, a balcony door across a stair top, a boathouse door between wall and boat, a shed door over its boxes, a cottage's front door on its partition and its bed in the bedroom door's swing). Later the same way: a dining table's end chairs 1.2 m from the manor's through-door, a shop's aisles and desk at its side door, upstairs manor doors that opened across a gallery as wide as a door (they open into the rooms now), a switchback's slab steps that the mesh joined from the side (a low wall there now), cabin steps no wider than the doorway, and **a door whose open panel splits a small room** (a camp cabin, a cottage bedroom): those slide now, which no stand-in or player has to walk round.

## Toys

Every map has a dozen or more of dot-props' toys (balls, swings, see-saws, roundabouts, trampolines, bounce pads, lamps on a switch, a fountain, a bell, a horn, barrels, tyres, skittles), chosen for the place: the school's bell is in its playground, the ship's bell and horn are on its forward deck, the castle has skittles in the courtyard and a herald's horn by the gate. Each map's are one `toys_<map>` function near the end of the builder, run by `Map.build` (`TOY_PLANS`). **A toy is a hint, settled**: `_settle_toys` moves one whose spot is taken (a prop, a wall, a door's swing, a spawn, another toy, no floor under a corner) to the nearest clear spot within its `near`, and stops the build when there is none. A bowling set settles as one. A trampoline and a bounce pad are checked against the room their flight needs (`TOY_CLEAR`), so neither can throw a hider onto a roof meant to be reached another way. `--spots <map> [y]` prints the open floor. game-hide-n-seek's `headless_maps` checks every toy stands clear and is still where it was put two seconds on.

## Things the builder decides for you

- **Spawns are settled and faced** (mg-prop-hunt-maps' finding, kept): a spawn a prop later landed on is moved to the nearest clear floored spot and printed; every spawn faces the middle of the furniture.
- **A pond is a hole in the field** (`floor_around`), with a step out at one corner: a pond bed 1 m down is more than a player steps.

## Checking

From game-hide-n-seek: `./game.sh test headless_maps headless_climb`, and look at what you changed (`tools/shot.sh --view=overview --hs-map-ids=<id>`, `--view=room --at=... --look=...`). The coordinates in `headless_climb`'s `CLIMBS` are this builder's; move a staircase here and move its climb there.

**What rendering found** (2026-10-10): the warehouse at dusk was washed orange by fog 0.004 over a sunset horizon, the same thing that happened to mg-prop-hunt-maps' woods; fog 0.0012 and a paler horizon read as dusk. Later the same day, with doors in, **every building's floor was striped with the grass under it**: a room's floor and the field both had their tops at y 0. The ground round them is 2 cm lower now (`GROUND`); the floors stayed where they were, because raising them buried every rug (`headless_maps` said so).

**What the door checks found that had been wrong all along** (2026-10-10): the manor's lounge sofa stood in the kitchen-to-lounge doorway, so that door was never passable (the door moved to the hall end of its wall); and every village cottage's garden fence ran along the house wall across the back door, so no back door ever led anywhere (`fence(skip=...)` leaves out the side a house is).

## Publishing

**This repository is a pack of its own, mounted on the server alone.** A server owner names it (`gamemann/hide-n-seek-maps`) in their map config; the game adds that mount's `maps/` to its catalogue (`HsModule._add_delivered_maps`). It is never in a client's content sync: a client is sent the one document being played. `.github/workflows/release.yml` runs `build_maps.py --check` and then dot-ci's release with `pack: true`. dot-server-deploy's `content/hideseek_maps/` and `examples/hideseek_client` (22 checks) prove the delivered pair works over a real socket.

`project.godot` exists only so dot-ci's release can import this repository and attach the `-pack.zip`; it carries the editor's own header and no comments, because the editor rewrites the file on its first save and a checkout would show it modified.

**Not yet on GitHub** (2026-10-10): the remote is set, the repository is the owner's to create, and nothing is pushed or published.

**`provides.json` is what lists this pack in the Workshop, and `tools/build_provides.py` writes it** from the documents in the folder (an entry per map, `map_kind` and the game it is for are constants at the top). Edit the script, run it, commit both; `--check` (CI) fails on a stale file. A pack whose newest READY version has no `provides` block is not a Workshop item. It ships because the pack takes every file outside the excluded folders.

## Ambience level

Every document carries `"ambient": {"level": n, "lively": true}` for dot-audio's `DotAmbienceDirector` (`level` multiplies the stock bed, 1.0 unchanged; `lively` lets the birdsong bed play): interiors 0.6-0.9 (a quiet room tone), open ground 1.0-1.5, and `lively` (birdsong) only on the park, camp, village and front yard. The table is `AMBIENT` in `tools/build_maps.py`, which writes it into built documents; `tools/build_maps.py --ambient` patches the imported ones, so a new map adds a row there.
