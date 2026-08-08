# FR: Make the node handle big-network traffic (public TCP hub scale)

**Status:** measured on-device 2026-08-08 · fixes implemented upstream in uP-reticulum
**Symptoms:** everything slow on `rns.varnatransport.com:4242`; nomadnet pages fail with
"path not found" / "link can't be established" while nodes are visible in the NET list.

## Root cause (measured, not guessed)

The Ed25519/X25519 C module executes from **flash XIP** since the natmod→built-in
conversion (done to free IRAM for WiFi). On the ESP32-S3 that costs **83×**:

| operation | builtin (flash XIP, -O2) | natmod (IRAM, -Os) |
|---|---|---|
| Ed25519 verify | 1461 ms | **17.6 ms** |
| Ed25519 sign | 871 ms | **12.0 ms** |
| X25519 exchange | 1120 ms | **12.4 ms** |

Same Monocypher sources, byte-identical outputs. The penalty is cache thrash: the
S3's 16 KB icache / 32 KB dcache serve flash code+rodata *and* the PSRAM heap;
Monocypher's inner field arithmetic misses continuously.

Every announce from an unknown destination costs one verify, synchronously, inside
the TCP poll loop. A public hub delivers a steady stream of new destinations
(449 known and counting; ~29 distinct announcers per minute). Instrumented live
session (110 s): **85.5 s inside `Transport.inbound`** (~78 % duty), 73 s of it
announce verification, event-loop stalls up to 3.1 s. Link proofs and path
responses queue behind the verifies → link timeouts and path-request failures.

Live A/B on the same hub, crypto hot-swapped to the IRAM natmod:

| | stock | IRAM crypto |
|---|---|---|
| link establishment | 11.2 s | **0.29 s** |
| index.mu fetch | 979 B / 16.9 s (57 B/s) | 6249 B / 0.52 s (**12 KB/s**) |
| loop stall max | 3100 ms | 238 ms |

**WiFi + natmod now coexist** — WiFi buffers moved to PSRAM in the current
firmware; verified natmod load → WLAN connect → TCP session with no
`ESP_ERR_NO_MEM`. The original reason for dropping the natmod is gone.

## Fixes (upstream urns, in priority order)

1. **IRAM-natmod-first crypto loader** — `crypto/ed25519.py` tries
   `ed25519_iram` (a natmod `.mpy` on the VFS, loads into IRAM) before the
   built-in XIP module; built-in stays as fallback so a missing file can never
   repeat the v1.1.0 no-radio incident. Ship `firmware/lib/ed25519_iram.mpy`.
   *Impact: 83× on all announce verify / link crypto — the headline fix.*
2. **TCP read path** — poll loop read 512 B per 10 ms tick with a Python call
   per byte (~12 KB/s effective, CPU-bound). Now: drain socket until EAGAIN
   with a 2 KB buffer, frame-scan with `find(0x7E)` + two-pass `replace()`
   unescape (C speed), per-iteration byte budget so the loop still yields.
   *Impact: ~5–10× on page/resource transfer; reader no longer starves.*
3. **Announce queue** — announces (the only heavy-and-deferrable packet type)
   are queued (bounded) and verified from `job_loop` under a time budget
   instead of synchronously in the interface read path. Link proofs, data and
   path traffic no longer wait behind crypto.
4. **Clock-jump path cull (bug)** — `_apply_clock` sets the RTC (+26 y from the
   year-2000 epoch) without re-stamping `path_table` / `reachable_destinations`
   / receipts / links, so every pre-sync path expires instantly and is culled
   within 5 s of the boot time sync. Now all pre-sync bookkeeping shifts by the
   sync delta.
5. **Table sizing for 8 MB-PSRAM boards** — `MAX_PATH_TABLE=32`,
   `MAX_DESTINATIONS=64`, `MAX_PACKET_CACHE=32` were Pico-W sizing; on the hub
   the path table churns permanently ("path not found" for anything not
   announced in the last minute). ESP32: 256 / 512 / 64; others keep the small
   defaults.
6. **known_destinations growth cap** — unbounded (449 entries / 123 KB JSON;
   4.5 s flash write at shutdown, 0.46 s load at boot). LRU-capped
   (768 on ESP32, 160 elsewhere), evicted in batches.
7. **Hot-path `gc.collect()` removal** — 2× per announce in `_handle_announce`
   + 2 in `validate_announce` (~13 ms each here, worse as heap fills): now only
   run when the pure-Python crypto fallback is active (their original purpose).

## Not in this round (candidates)

- sdkconfig icache 32 KB / dcache 64 KB — would speed *all* flash code (VM,
  display, JPEG) at the cost of 48 KB internal SRAM; needs its own A/B.
- `Identity.recall(from_identity_hash=True)` linear scan does a sha256 per
  known destination; fine at current sizes.
- GUI repaint per announce; second-order once announce handling is cheap.

## Validation

Whole-`urns/` VFS shadow + `/lib/ed25519_iram.mpy` on the T-Deck, live against
`rns.varnatransport.com:4242`: loader picks the natmod, link + page fetch at
IRAM-crypto speed, announce queue drains within budget, no WiFi regression.
(Frozen-ROM build still needs a firmware rebuild + reflash to ship.)
