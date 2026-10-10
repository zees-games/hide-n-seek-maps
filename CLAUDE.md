# hide-n-seek-maps

The maps game-hide-n-seek plays, as JSON documents. Read `../../CLAUDE.md` and `../game-hide-n-seek/CLAUDE.md` first: the format is defined and validated by game-hide-n-seek's `game/hs_map_doc.gd`, and `maps/README.md` is the mapper's copy of it.

**`maps/` is what the game reads**, through a dot-bootstrap link (`game-hide-n-seek/.gitignore` names it: `# bootstrap-link: hide-n-seek-maps/maps`). The game reads every `.json` there plus the practice house built into it; a refused document is listed by `hs_maps` with the reason, and every other map still plays.

**`tools/build_maps.py` writes every document, and the JSON is committed.** Edit the builder, run it, commit both. `--check` (CI) fails when a committed file differs from what the builder writes. The library half (walls, rooms, `against`, spawn settling and facing) came from mg-prop-hunt-maps' builder on 2026-10-10; what is this repository's own is the hide-and-seek half below it.

**Prop sizes are a snapshot** (`tools/prop_sizes.json`), copied from game-hide-n-seek's `props/catalogue.json` by `--sync-sizes`, so the builder (and CI) runs without the game checked out.

## What a hide-and-seek map needs, and the helpers that make it

A map is judged by three things the genre's best maps share: **more than one way round** (a chase into a dead end is over), **somewhere up** (a roof, a loft, a gallery, a rack), and **somewhere a seeker has to crouch to look** (a crawl space, a rack's bottom shelf, the space under a bridge). Every map here has all three, and the builder has one call for each part:

- `flight(m, x0, x1, z0, z1, y0, y1, ascend)`: a straight flight. **Steps are slabs 0.3 m deep, not blocks to the floor**, because a switchback stairwell's upper flight passes over the lower one: a solid block left 1.7 m of headroom at the turn, under a person's height. 0.22 m a step (a player steps 0.4).
- `crawl(...)`: a tunnel 1.1 m high, open at both ends. A person crouched is 0.95 m, standing 1.8 m. `headless_climb` tries one standing (refused) and crouched (through).
- `rack(...)`: warehouse shelving whose first shelf's underside is 1.27 m over a 0.15 m deck, so every bottom bay is a crawl space, with boxes in some bays and not others.
- `container(...)`: a shipping container, walkable on top, optionally open at one end.
- `hedge_maze(...)`: a seeded depth-first maze, the same every build (`random.Random(seed)`), gates on two sides.
- `room_at(...)`: `Map.room` for an upper storey or a mezzanine.

## Things the builder decides for you

- **Spawns are settled and faced** (mg-prop-hunt-maps' finding, kept): a spawn a prop later landed on is moved to the nearest clear floored spot and printed; every spawn faces the middle of the furniture.
- **A pond is a hole in the field** (`floor_around`), with a step out at one corner: a pond bed 1 m down is more than a player steps.

## Checking

From game-hide-n-seek: `./game.sh test headless_maps headless_climb`, and look at what you changed (`tools/shot.sh --view=overview --hs-map-ids=<id>`, `--view=room --at=... --look=...`). The coordinates in `headless_climb`'s `CLIMBS` are this builder's; move a staircase here and move its climb there.

**What rendering found** (2026-10-10): the warehouse at dusk was washed orange by fog 0.004 over a sunset horizon, the same thing that happened to mg-prop-hunt-maps' woods; fog 0.0012 and a paler horizon read as dusk. Nothing else needed changing after the first renders.

## Publishing

**This repository is a pack of its own, mounted on the server alone.** A server owner names it (`gamemann/hide-n-seek-maps`) in their map config; the game adds that mount's `maps/` to its catalogue (`HsModule._add_delivered_maps`). It is never in a client's content sync: a client is sent the one document being played. `.github/workflows/release.yml` runs `build_maps.py --check` and then dot-ci's release with `pack: true`. dot-server-deploy's `content/hideseek_maps/` and `examples/hideseek_client` (22 checks) prove the delivered pair works over a real socket.

**Not yet on GitHub** (2026-10-10): the remote is set, the repository is the owner's to create, and nothing is pushed or published.
