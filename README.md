# The Isle of Wandering Gold

A treasure-map generator for the make.fun bounty
**Charm the Cartographers: A One Day Treasure Map Sprint**
(`u-ravendell-charm-the-cartographers-a-one-day-00854547`).

`main.py` draws one 1024x1024 PNG of a fictional island — the Isle of
Wandering Gold — and writes it to `/output/entry.png` (or `./output/`
when `/output` is not writable). The map has nine labeled places that
belong to one story: a captain's wreck, a grumbling volcano, a whispering
jungle, parrot woods, a mermaid lagoon, a dead man's cove, an old landing,
a skull rock, and the captain's cache, marked with a big red X at the end
of a dotted route. A compass rose, a sea serpent, a kraken, and a few
small jokes ("Do not feed the parrots", "Here be monsters") fill the sea.

Pure Python standard library — no Pillow, no network, no font files.
The picture is drawn with a tiny software rasterizer (thick polylines,
scanline polygon fill, bezier curves) and every letter uses a
single-stroke engraved-capitals font defined in the file, so the program
runs in any python3.13 sandbox. Output is an indexed PNG well under the
1 MiB gate.

Run it:

```
python3 main.py
```
