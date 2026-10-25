#!/usr/bin/env python3
"""The crop (x0,z0,x1,z1,ymin,ymax) in a world pack's own frame that reduces it to a map document's playable boxes.

    tools/world_crop.py <id> [margin_m]

The document's solid, visible boxes' bounds, less its `world.offset`, plus a margin (1 m default); `regen_worlds.sh`
hands it to dot-map's `bsp.py regen --crop` for a worlds.tsv row whose crop column is `auto`.
"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)


def crop(mid, margin=1.0):
    doc = json.load(open(os.path.join(ROOT, "maps", mid + ".json")))
    off = doc["world"]["offset"]
    bs = [b for b in doc["boxes"] if b.get("solid", True) and b.get("visible", True)]
    lo = [min(b["at"][i] - b["size"][i] / 2 for b in bs) - off[i] for i in range(3)]
    hi = [max(b["at"][i] + b["size"][i] / 2 for b in bs) - off[i] for i in range(3)]
    return [lo[0] - margin, lo[2] - margin, hi[0] + margin, hi[2] + margin, lo[1] - margin, hi[1]]


if __name__ == "__main__":
    print(",".join("%.2f" % v for v in crop(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 1.0)))
