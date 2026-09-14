# Server performance tools (GTNH, Forge 1.7.10)

These scripts analyse async-profiler `collapsed` output and region files from a Forge 1.7.10 GTNH server; you supply your own profiles.

**Prerequisites:** Python 3; `nbtlib==2.0.4` for the region-reading scripts `ents.py` and `chunk.py`; async-profiler to capture profiles; profiles are NOT included.

| Script | Description |
|---|---|
| `an2.py <profile>` | attribution per subsystem (buckets using 1.7.10 SRG names) plus the roots of the stacks. |
| `tree.py <profile> <frame> [depth] [min%]` | call tree from a given frame. |
| `callers.py <profile> <frame>` | who calls a hot method. |
| `diff.py <profileA> <profileB>` | bucket-by-bucket difference and the biggest self-time gains/losses. |
| `ents.py [dims...]` | walks `World/**/region/*.mca` and counts entities/tile entities per type and per chunk (with coordinates). Run it AFTER a `save-all`. Needs `nbtlib`. |
| `forced2.py` | decodes every `forcedchunks.dat` (ForgeChunkManager tickets per dimension and per mod). |
| `chunk.py` | opens specific chunks and lists entities per type with Y and an example coordinate, plus the chunk's tile entities. Edit the target list at the top. Run after a `save-all`. |
| `cpu_per_tick.sh [seconds]` | the metric that actually matters: real CPU cost per tick, measured via `/proc/<pid>/stat`. |

### Why not /forge tps
`/forge tps` and `/cofh tps` only sum the world tick of each dimension and do not count `ServerTickEvent`, where AE2 updates its grids; in one measured case the game reported 28.66 ms/tick while the real cost measured from `/proc` was 40.55 ms/tick (81.1% of one core).
