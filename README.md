The maps for [game-hide-n-seek](https://github.com/zees-games/game-hide-n-seek): eleven maps of our own, nine maps converted from community work, and a practice house built into the game.

A map is a JSON document, not a scene. The server reads every `.json` file in its map directory and sends the current map to each client, so players need no map files. To add a map, drop one file in the directory. The format is in [maps/README.md](maps/README.md).

## Using it

[dot-bootstrap](https://github.com/modcommunity/dot-bootstrap) clones this repository next to game-hide-n-seek and links it in as `maps/`. Run `./game.sh` in game-hide-n-seek and you play these maps. On a server they are delivered as one pack (`hideseek_maps`), which `./game.sh online` and `./game.sh server` build for you.

## Our maps

Every map has a loop to run round, a high place, a spot a seeker must crouch to look into, and doors you open and shut with E. Each also has one sliding wall panel.

| Map | What is in it |
| --- | --- |
| `hs_apartments` (Larkspur Court) | Three floors of flats, a stairwell up to the roof, a fire escape, a back yard, a corner shop, parked cars |
| `hs_warehouse` (Dockside Warehouse) | Racks you can crawl under, crate mazes, a mezzanine office, a container yard on the quay |
| `hs_manor` (Hollowmere Manor) | Two-storey house, eight rooms, a hedge maze, a garden with a greenhouse and gazebo |
| `hs_village` (Thistle Green) | Six cottages round a square, a barn with a hayloft, a meeting hall, a pond with a bridge |
| `hs_school` (Brookside School) | Two floors of classrooms, lockers, a cafeteria, a gym with hollow bleachers |
| `hs_mall` (Galleria Mall) | Two floors of shops round a glass atrium, a food court, a cinema, service corridors |
| `hs_ship` (The Marigold) | A four-deck ship at a quay: engine room, cabins, dining room, sun deck with a pool |
| `hs_castle` (Ravenhold Castle) | Curtain wall, corner towers, gatehouse and moat, a three-floor keep |
| `hs_camp` (Pinehollow Camp) | Cabins on blocks, a mess hall, tents, a fire lookout, a treehouse, a dock |
| `hs_museum` (Halden Museum) | Two storeys round a rotunda with a whale skeleton, galleries, a dinosaur hall |
| `hs_park` (Pinecrest Park) | Play towers and tubes, toilets, a tennis court, a pond, a ranger's cabin, woods |

## Credits

The converted maps keep their authors' names in the document's `author` and `source` fields. Each map's licence entry is in [licence.json](licence.json).

| Map | Original | Author | Source | Licence |
| --- | --- | --- | --- | --- |
| `hs_apartment` | ph_apartment | Tib | [GameBanana](https://gamebanana.com/mods/61479) | CC-BY-NC-ND-4.0 |
| `hs_frontyard` | ph_frontyard | axoh | [GameBanana](https://gamebanana.com/mods/398632) | none stated |
| `hs_hotel` | ph_hotel | 1Vodker1 | [GameBanana](https://gamebanana.com/mods/61495) | CC-BY-NC-ND-4.0 |
| `sunken_resort` | Sunken Resort | Praisemantas | [Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3376822715) | none stated |
| `hs_restaurant` | ph_restaurant_os | Wolvindra Vinzuerio | [Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3270930065) | none stated |
| `deco_mansion` | Gm_Deco_Mansion | Gastly_Furby_777 | [Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3698815153) | none stated |
| `functionalist_night` | Funkis (Night) | VITAPLEX | [Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3382694345) | none stated |
| `gm_villa` | a Workshop map | korka007 | [Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=2633639653) | none stated |
| `hs_mountainresort` | gm_mountainresort | prvec | [Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=2312309380) | none stated |

Only the box layout, doors and spawns are converted. Art is replaced with CC0 Kenney props. One author's licence note is quoted in [docs/import-notes.md](docs/import-notes.md).

If you made one of these maps and want it removed, open an issue at <https://github.com/zees-games/hide-n-seek-maps/issues> and it goes.

## Building the maps

`tools/build_maps.py` writes every own-map document in `maps/`. Walls, doors, rooms, stairs and whole hedge mazes are one call each.

```bash
python3 tools/build_maps.py                # rewrite maps/*.json
python3 tools/build_maps.py --check        # exit 1 if a file differs from the builder's output
python3 tools/build_maps.py --sync-sizes   # copy prop sizes from ../game-hide-n-seek
```

The JSON is committed and CI runs `--check`, so hand edits to a built map are caught. Hand-written documents are fine too: any `.json` file in `maps/` loads.

Converted maps come from `tools/import_bsp.py`, which turns a Source 1 `.bsp` into a document. The flags used for each map are in [docs/import-notes.md](docs/import-notes.md). The game's `examples/headless_maps.tscn` checks every map (size on the wire, known props, spawns with floor and headroom, enough cover, doors that swing).

## Drawn worlds (local only)

`tools/regen_worlds.sh` can build a drawn world for a map (using dot-map's `bsp.py regen`) into `world/`. Those packs carry the source author's own art and lighting, so they are never published: `tools/publish.sh` is the only publish command and it skips `world/`. The NC-ND maps (`hs_hotel`, `hs_apartment`) stay local until the owner decides.

## Licence

MIT, see [LICENSE](LICENSE). Credited community maps keep the licences listed above.
