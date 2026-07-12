# T-Deck delta vs upstream uP-reticulum

`lib/urns/` is vendored from [uP-reticulum](https://github.com/varna9000/micropython-reticulum)
`firmware/urns/` at commit **`b2bc4f7`** ("lxmf: register announce app_data via
set_default_app_data"). This file lists every deliberate difference from that
base so the next re-sync is a mechanical repeat of the same procedure.

## Re-sync procedure

1. Copy upstream `firmware/urns/**/*.py` over `lib/urns/` wholesale.
2. Re-apply the delta below (each item is small and self-contained).
3. Run the host tests: `python3 lib/tests/test_transport.py`,
   `test_lora_lbt.py`, `test_lora_split.py` — all must pass.
4. Recompile `lib/urns/**/*.mpy` with the in-tree
   `tools/firmware_build/micropython/mpy-cross/build/mpy-cross -march=xtensawin`.
5. Rebuild the firmware (`bash tools/build_firmware.sh`) — urns is frozen into
   ROM from `lib/urns` — and reflash the app partition
   (`esptool.py write_flash 0x10000 micropython.bin`, **no erase**).
6. Update the base commit at the top of this file.

## The delta

### `interfaces/lora.py` — hardware bring-up on shared SPI

The T-Deck's SX1262 shares SPI1 with the ST7789 display and regularly misses
the first init after a cold boot. Upstream has no reset/retry logic.

- `_reset_hw()` — pulse the SX1262 RESET pin (low 20 ms, high 50 ms)
- `_init_with_retry(max_attempts=3)` — replaces the bare `_init_modem()` call
  in `__init__`; hardware reset + growing delay between attempts
- `poll_loop()` offline-recovery preamble — if init failed, retry with
  backoff `[2, 5, 10, 20, 30]` s (reset + reinit inside `_acquire`/`_release`)
  before giving up
- `on_status` config key — `callback(online: bool)`, fired after successful
  init/recovery; drives the UI LoRa indicator
- Debug-log gating (`_log_debug`) in `poll_loop` + GC/diag interval raised
  10 s → 30 s (GC pauses are audible during voice recording)

### `link.py` — multi-hop LoRa timing + app hooks + MicroPython GC

- `Link.ESTABLISHMENT_TIMEOUT` 25 → **75** s, `Link.CREATION_COOLDOWN`
  15 → **5** s, `OutgoingLink.ESTABLISHMENT_TIMEOUT` 30 → **75** s
  (multi-hop LoRa: ~30 s RTT + ~5 s ESP32 ECDH + margin)
- `Link.resource_started_callback` field, invoked from
  `Link._handle_resource_adv()` when a resource is accepted (progress UI)
- Circular-ref-breaking blocks at the end of `Link.teardown()` and
  `OutgoingLink._close()` — MicroPython GC cannot collect reference cycles
- The "Link request on …" log is a single `%`-format expression: the
  multi-line `+` concat raises `str + bytes` TypeError **only when frozen**
- Enriched decrypt-failure log in `Link.receive` (adds ctx byte + length)

### `resource.py` + `lxmf.py` — incoming transfer progress

- `Resource.accept()` sets `r.progress_callback = None`; `receive_part()`
  invokes it (guarded) as `callback(received_count, total_parts)`
- `LXMRouter.register_progress_callback(cb)` + `_on_resource_started()` wire
  the router-level callback into each accepted resource via
  `link.resource_started_callback`

### `transport.py` — hot-path cost on ESP32

- `packet_hashset` — a `set` mirror of `packet_hashlist` (which stays for FIFO
  eviction); `_cache_packet_hash()` and `packet_filter()` do O(1) membership
  instead of scanning a 512-entry list per packet
- `_log_debug` module flag gates the two per-packet debug logs in
  `outbound()`/`inbound()` (skips log-string building; evaluated at import)

### `lib/tests/` — two fork adjustments to the imported upstream suite

- `harness.py` `reset_transport()` also clears `packet_hashset`
- `test_filter_dedups_data` remembers hashes via `Transport._cache_packet_hash()`
  instead of appending to `packet_hashlist` directly

## Dropped tdeck patches (superseded by upstream)

Kept here so nobody re-applies them from old notes:

- **HDR_2 auto-routing in `packet.py`/`tcp.py`** and **resource request
  retry** — both upstreamed before the current base (see upstream
  `dc60334`/`7d762ee`); upstream's directed-routing Transport (`a09e841`)
  replaced the rest
- **Roaming path semantics** (`PATH_EXPIRY_ROAMING`, MODE_ROAMING default,
  no-clear-on-HDR_1) — replaced by upstream `should_add` (hop count + emission
  timestamp), 24 h `PATH_EXPIRY`, and table culling
- **2 s `job_loop` cadence** — upstream's 0.25 s tick now services path-request
  waiters and announce jitter; per-tick work is trivial on a leaf node. If GC
  pauses reappear during recording, tune upstream, don't re-fork.
