The maps for [**game-hide-n-seek**](https://github.com/gamemann/game-hide-n-seek): a block of flats with a roof and a fire escape, a warehouse on the quay, a manor with a hedge maze, a village, and a park. Five maps to start with, plus the practice house built into the game.

Every map is a **JSON document**, not a scene. A server reads the documents in its map directory and sends the one it is playing to every client when the map changes, so players need no map files at all and a new map is one file dropped into a directory. [`maps/README.md`](maps/README.md) is the format.

## Using it

[dot-bootstrap](https://github.com/modcommunity/dot-bootstrap) clones this repository next to game-hide-n-seek and links it into the game as `maps/`, so `./game.sh` in game-hide-n-seek plays these maps with nothing else to do. On a server they are delivered as a pack of their own (`hideseek_maps`), which game-hide-n-seek's `./game.sh online` and `./game.sh server` build for you.

## The maps

Every map has a way round (a chase that reaches a dead end is over), somewhere up, and somewhere a seeker has to crouch to look into.

| Map | What it is |
| --- | --- |
| `hs_apartments` (Larkspur Court) | Three floors of flats joined by a switchback stairwell that goes on up to the roof, a fire escape down the west side into the alley, a back yard with a bike shelter, a corner shop with a back room, and a street of parked cars to crouch behind |
| `hs_warehouse` (Dockside Warehouse) | Four rows of racks whose bottom shelves you can crawl into, a maze of crate stacks, a mezzanine office up a flight of stairs, and a container yard on the quay at dusk, some containers open at one end and one with steps to its roof. The quay's edge is a fall into the harbour |
| `hs_manor` (Hollowmere Manor) | A two-storey house with a grand stair and a gallery round the hall, eight rooms, a hedge maze to the west, and a garden with a greenhouse, a gazebo and a shed |
| `hs_village` (Thistle Green) | Six cottages round a square with a fountain and market stalls, each with a back door and a fenced garden, a barn with a hayloft, a meeting hall, and a pond with a bridge you can crouch under |
| `hs_park` (Pinecrest Park) | A playground of towers, a rope bridge and play tubes you crawl through, a picnic shelter, the toilets, a fenced tennis court, a pond with a footbridge, a ranger's cabin and the woods all round |

## How they are made

`tools/build_maps.py` writes every document in `maps/`. A wall with its doors and windows, a room with its floor, ceiling and lights, a flight of stairs, a crawl space and a whole hedge maze are one call each, and every prop is placed by where it stands.

```bash
python3 tools/build_maps.py                # rewrite maps/*.json
python3 tools/build_maps.py --check        # exit 1 if a file differs from what the builder writes
python3 tools/build_maps.py --sync-sizes   # copy prop sizes from ../game-hide-n-seek after it measures new models
```

The JSON is still what the game reads and is committed; CI runs `--check`, so a hand edit to a built document is caught instead of being lost on the next build. Hand-written documents the builder does not produce are fine too: anything in `maps/` with a `.json` extension is loaded.

The builder moves any spawn that a prop or a wall was put on top of to the nearest clear spot and says so. Every map is then checked by game-hide-n-seek's `examples/headless_maps.tscn` (it fits on the wire, every prop is one the game knows, every spawn has a floor and room for a person, no prop is sunk into a wall, every prop stands on something, and there is enough to hide behind), and its stairs and crawl spaces are walked by `examples/headless_climb.tscn`.

## License
MIT. See [LICENSE](LICENSE).
