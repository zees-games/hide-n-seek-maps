The maps for [**game-hide-n-seek**](https://github.com/zees-games/game-hide-n-seek): a block of flats with a roof and a fire escape, a warehouse on the quay, a manor with a hedge maze, a village, a park, a school, a shopping mall, a ship, a castle, a summer camp and a museum. Eleven maps, plus the practice house built into the game.

Every map is a **JSON document**, not a scene. A server reads the documents in its map directory and sends the one it is playing to every client when the map changes, so players need no map files at all and a new map is one file dropped into a directory. [`maps/README.md`](maps/README.md) is the format.

## Using it

[dot-bootstrap](https://github.com/modcommunity/dot-bootstrap) clones this repository next to game-hide-n-seek and links it into the game as `maps/`, so `./game.sh` in game-hide-n-seek plays these maps with nothing else to do. On a server they are delivered as a pack of their own (`hideseek_maps`), which game-hide-n-seek's `./game.sh online` and `./game.sh server` build for you.

## The maps

Every map has a way round (a chase that reaches a dead end is over), somewhere up, somewhere a seeker has to crouch to look into, and doors: every room, cupboard, stall, gate and container has one you open and shut with E, and somewhere on each map is a panel of wall that slides.

| Map | What it is |
| --- | --- |
| `hs_apartments` (Larkspur Court) | Three floors of flats joined by a switchback stairwell that goes on up to the roof, a fire escape down the west side into the alley, a back yard with a bike shelter, a corner shop with a back room, and a street of parked cars to crouch behind. A front door, a bedroom door and a bathroom door to every flat, fire doors onto the escape, and on the top floor a loose panel between two bedrooms |
| `hs_warehouse` (Dockside Warehouse) | Four rows of racks whose bottom shelves you can crawl into, a maze of crate stacks, a mezzanine office up a flight of stairs, and a container yard on the quay at dusk, where every open container has a pair of doors to shut yourself in with and one has steps to its roof. Roller shutters on the loading bays. The quay's edge is a fall into the harbour |
| `hs_manor` (Hollowmere Manor) | A two-storey house with a grand stair and a gallery round the hall, eight rooms with doors, a cupboard under the stair that only a sliding panel gets you into, a hedge maze to the west, and a garden with a greenhouse, a gazebo and a shed |
| `hs_village` (Thistle Green) | Six cottages round a square with a fountain and market stalls, each with a front door, a back door and a garden gate, a barn with a hayloft whose doors open over the drop, a meeting hall with a hollow stage you get under through a loose board, and a pond with a bridge you can crouch under |
| `hs_school` (Brookside School) | Two storeys of classrooms off one corridor lined with lockers, with a switchback stairwell at each end (so a chase on one floor goes round by the other), a cafeteria and kitchen, toilets with stall doors on both floors, a gym as tall as the building with hollow bleachers, a storeroom behind a shutter and a balcony reached from the top corridor or a stair from the floor, a library whose archive is behind a sliding panel, and outside a portable classroom on blocks with a crawl space under it, a bike shed and a playing field |
| `hs_mall` (Galleria Mall) | Two floors of twelve shops each round a glass-roofed atrium with two stairs and two bridges across it; shops behind roller shutters (two of them down) and glass doors, with changing rooms, shelves and counters; a food court and a supermarket below, a cinema with hollow tiered seats and a furniture showroom above; service corridors behind every shop's back door with a stair at each end; the projection booth behind a panel of the cinema's back wall; a car park and a loading dock |
| `hs_ship` (The Marigold) | A ship moored at a quay, four decks: an engine room of machinery with a crawl tunnel and the steering-gear room behind a sliding panel in its after bulkhead; a deck of cabins off one long corridor; the dining room, lounge and lobbies with promenades down both sides; the sun deck with a pool, open lifeboats you can crouch in, the funnel and the bridge, with a stair to its roof. Two switchback stairwells run from the engine room to the sun deck, and stairs come down at both ends. Two gangways from the quay; over the side is the harbour, and a catch |
| `hs_castle` (Ravenhold Castle) | A curtain wall with a walk along its top and merlons, a tower at each corner with a switchback inside from the courtyard to the walk and the top, stairs up the inside of two walls, a gatehouse with gates and a portcullis over a moat (shallow: steps lead out of it) and a postern in the north wall; a keep of three floors (great hall and kitchen, lord's chamber and library, barracks, and its roof) over a dungeon of barred cells; stables with half-height stall gates, a smithy and a well. A panel of loose stones in the dungeon's guard room opens on a tunnel under the courtyard that comes up through the floor of the stables' tack room |
| `hs_camp` (Pinehollow Camp) | A summer camp in the woods by a lake: four cabins on blocks (a crawl space under each) with bunk beds, a mess hall with its kitchen, outhouses, tents round a campfire, a fire lookout with a switchback in an open frame up to a cabin twelve metres up, a treehouse with a hut and a rope bridge to a second platform, a dock with two rowing boats and a boathouse with doors on the water, an archery range, and a cave of passages in the hillside with a crawl-in back way and a chamber behind a rock that slides |
| `hs_museum` (Halden Museum) | Two tall storeys round a rotunda with a skylight and a whale's skeleton hung in it, grand stairs either side of the entrance hall up to a gallery that rings it; below, a dinosaur on a plinth whose ribs stand high enough to crouch under, a stepped pyramid with a crawl tunnel through its base and a tomb with a stone door, an art gallery that is a maze of partitions, a cafe and a gift shop, a security office and a loading store with a shutter; above, an armour hall of glass cases, a space gallery with a rocket and a capsule with a hatch, a hall of clothes on stands with changing rooms, a conservation lab, and a library whose vault is behind a panel of its wall |
| `hs_park` (Pinecrest Park) | A playground of towers, a rope bridge and play tubes you crawl through, a picnic shelter, the toilets with a door on every stall and loose tiles between the two sides, a fenced tennis court with a gate, a pond with a footbridge, a ranger's cabin and the woods all round |
| `hs_frontyard` (Front Yard) | A suburban house and its front yard, imported from a Source map (see Credits): a lawn with a hedge, fences and a shed to hide behind, and a house whose doors open and shut. |

## How they are made

`tools/build_maps.py` writes every document in `maps/`. A wall with its doors (and the panels in them) and windows, a room with its floor, ceiling and lights, a flight of stairs, a crawl space and a whole hedge maze are one call each, and every prop is placed by where it stands.

```bash
python3 tools/build_maps.py                # rewrite maps/*.json
python3 tools/build_maps.py --check        # exit 1 if a file differs from what the builder writes
python3 tools/build_maps.py --sync-sizes   # copy prop sizes from ../game-hide-n-seek after it measures new models
```

The JSON is still what the game reads and is committed; CI runs `--check`, so a hand edit to a built document is caught instead of being lost on the next build. Hand-written documents the builder does not produce are fine too: anything in `maps/` with a `.json` extension is loaded.

The builder moves any spawn that a prop or a wall was put on top of to the nearest clear spot and says so. Every map is then checked by game-hide-n-seek's `examples/headless_maps.tscn` (it fits on the wire, every prop is one the game knows, every spawn has a floor and room for a person, no prop is sunk into a wall, every prop stands on something, there is enough to hide behind, every door swings into free space and every doorway is clear), and its stairs and crawl spaces are walked by `examples/headless_climb.tscn`.

## Imported maps and credits

`tools/import_bsp.py` converts a Source 1 `.bsp` into a document here (brushes become boxes, props become catalogue props, `prop_door_rotating` becomes a real door, player starts become seeker and hider spawns; art is replaced with the game's CC0 Kenney props). Imported documents are not written by `build_maps.py`; rerun the importer with the original `.bsp`. Flags: `--style`, `--slide-doors`, `--cover=N`, `--drop-kind=<id>`, `--thin=<id>:N`.

| Map | Original | Author | Source |
| --- | --- | --- | --- |
| `hs_frontyard` | ph_frontyard | axoh | https://gamebanana.com/mods/398632 |

The author's licence note: "do whatever idc just dont say this terrible map is urs". We credit the author in the map's `author` and `source` fields and will remove the map on request.

Not converted: `ph_apartment` (Tib, https://gamebanana.com/mods/61479) because its page forbids distributing a modified copy. `ph_hotel` (1Vodker1, https://gamebanana.com/mods/61495, CC BY-NC-ND 4.0) was converted but is too small for the bot suite's hider-survival bands (hiders were all caught in under 35 s), so it is not shipped.

## License
MIT. See [LICENSE](LICENSE).
