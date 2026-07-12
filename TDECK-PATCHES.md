# The Reticulum stack comes from a submodule — no local patches

`lib/urns/` is gone. The full µReticulum stack (`urns/`), the LoRa board
presets, `adc_reader`, and the host test suite live in the
**`vendor/uP-reticulum` git submodule**
([uP-reticulum](https://github.com/varna9000/micropython-reticulum)), pinned to
an exact commit. The firmware manifest (`tools/tdeck_manifest.py`) freezes
directly from `vendor/uP-reticulum/firmware/`.

Every T-Deck patch that used to live here was upstreamed (uP-reticulum commits
`2a39676`, `055a3a6`, `8927506`, `ce5b7a2`), three of them reshaped to match
reference RNS exactly:

- SX1262 hardware reset + init retry + poll-loop recovery, `on_status` hook
- Link establishment timeouts — now **per-hop** (35 s base + 20 s/hop), like
  reference `ESTABLISHMENT_TIMEOUT_PER_HOP`
- `resource_started_callback` + `Resource.progress_callback(resource)` +
  `LXMRouter.register_progress_callback` — reference-parity signatures
- Circular-ref breaking at link close (MicroPython GC), frozen-bytecode log fix
- Transport dedup via **rotating hash sets** (reference RNS shape, O(1))
- Debug-log gating + 30 s GC cadence in hot loops

The one thing that is *not* in the submodule is deliberate: nothing. If the
T-Deck ever needs an urns change, make it in uP-reticulum (guard it behind
config if board-specific) and bump the submodule — do not fork files here.

## Cloning

```bash
git clone --recursive git@github.com:varna9000/reticulum-tdeck.git
# or, in an existing clone:
git submodule update --init
```

## Updating the stack

```bash
git -C vendor/uP-reticulum pull origin master
python3 vendor/uP-reticulum/firmware/tests/test_transport.py   # 42 tests
python3 vendor/uP-reticulum/firmware/tests/test_lora_lbt.py    # 9
python3 vendor/uP-reticulum/firmware/tests/test_lora_split.py  # 8
bash tools/build_firmware.sh                                   # re-freeze
# flash app partition only (keeps the VFS):
#   esptool.py write_flash 0x10000 tools/firmware_build/micropython.bin
git add vendor/uP-reticulum && git commit -m "Bump uP-reticulum to <sha>"
```

If an update changes app-facing APIs, the touchpoints in this repo are all in
`tdeck_node.py` (LXMRouter callbacks, `send_message` return values, interface
config keys) and `tdeck_config.py` (`LORA_CONFIG` / `CONFIG` blocks).
