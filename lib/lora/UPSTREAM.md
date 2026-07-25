# `lib/lora/` — vendored micropython-lib LoRa driver

Verbatim copies of the upstream MicroPython LoRa driver packages. **Do not
edit these files.** Every T-Deck-specific workaround (SX1262 hardware reset,
init retries, DC-DC regulator mode, CSMA/listen-before-talk, RNode framing)
lives in `urns/interfaces/lora.py` in the `vendor/uP-reticulum` submodule, not
here — keeping these pristine is what makes the copies auditable.

| File | Upstream package | Version |
|---|---|---|
| `__init__.py`, `modem.py` | `lora` | 0.2.0 |
| `sx126x.py` | `lora-sx126x` | 0.1.5 |
| `sync_modem.py` | `lora-sync` | 0.1.1 |

Source: [micropython/micropython-lib](https://github.com/micropython/micropython-lib),
`micropython/lora/`, commit `09a33ee` (2026-02-18).

These were previously installed on the device with `mpremote mip install
lora-sx126x lora-sync`; they are vendored so a release build needs no network
and pins one exact driver revision. `mpy-cross` on these files reproduces the
`.mpy` files that revision of `mip` installed, byte for byte (`mip` appends a
`__version__` line — nothing in this project reads it).

`tools/tdeck_manifest.py` freezes all four into ROM. They are pure bytecode,
so unlike the native codec/crypto modules they need no filesystem copy.

## Updating

```bash
git clone --filter=blob:none https://github.com/micropython/micropython-lib
cp micropython-lib/micropython/lora/lora/lora/{__init__,modem}.py    lib/lora/
cp micropython-lib/micropython/lora/lora-sx126x/lora/sx126x.py       lib/lora/
cp micropython-lib/micropython/lora/lora-sync/lora/sync_modem.py     lib/lora/
bash tools/build_firmware.sh    # re-freeze, then flash and confirm the radio comes up
```

Update the table above with the new versions and commit.

**Pending upstream change not taken:** `lora-sx126x` 0.1.6 (micropython-lib
`9f7f99b`) adds `_IRQ_CRC_ERR` to the modem's IRQ mask, without which the
modem never reports the bit. On 0.1.5 that makes `_rx_flags_success()` accept
CRC-failed frames as good, so `modem.crc_errors` — the counter behind the
`crc_err=` field in the interface's 30 s `LoRa diag` line — is stuck at zero.
The RX data path is unaffected either way: `urns/interfaces/lora.py` sets
`rx_crc_error = True`, so corrupt frames are handed up regardless of the bit,
and Reticulum drops them on its own packet-hash check. Taking 0.1.6 fixes the
diagnostic; it is deliberately not bundled in this release because 0.1.5 is
the revision that has actually been flown on the hardware.
