# T-Deck Pro: sleep current measurements (2026-10-11)

Bench record of where the Pro's light-sleep current went and what fixed it.
The probe scripts named here are in this directory; each writes its readings
to `/measure.txt` on the deck.

## Result

| State, battery only | Before | After |
|---|---|---|
| Light sleep (`lowpower.sleep`, as the app does) | 36-40 mA | 5 mA |
| Awake, idle | 72 mA | 51 mA |

Probe conditions: the radio was left as `mpremote` left it and was **not
listening**. In the app the radio listens through sleep, which an earlier
bench (2026-10-09) put at about 10 mA. The app's own sleep current after the
fix has not been measured.

## Where the 33 mA went

| Cost | Cause | Fix |
|---|---|---|
| 20 mA, asleep and awake | GPIO 43 (the chip's default UART0 TX) left driven high into the GPS module's receive pin while the module's supply is off | `board_tdeck_pro.power_up()` parks it: `Pin(GPS_TX, IN, PULL_DOWN)` |
| 13 mA, asleep | I2S LRCK (GPIO 9) kept driven through sleep, high or low, keeps the PCM5102A side awake | isolated for each sleep, restored on wake: `sleep_isolate_pins()` -> `lowpower.configure()` |
| 0.5 mA, asleep | flash/PSRAM pads without ESP-IDF's sleep pull-ups | `SLEEP_MSPI_STANDBY = True` -> `lowpower.configure()` |
| 0.5 mA, asleep | e-ink booster left on after a refresh | not changed (a power-off after each refresh would slow refreshes) |

The ESP32-S3 itself is not the floor: stock `machine.lightsleep()` with every
pad isolated reads 7 mA for the whole board, and the RTC peripheral domain,
which the app keeps on, costs nothing measurable.

Two conclusions reached on the way were wrong and are corrected above:

- "The remaining ~20 mA is the flash/PSRAM pads" was an inference by
  elimination. Measured directly it is 0.5 mA.
- The 2026-10-09 bench listed the GPS as worth "1-3 mA" with its enable low.
  The module was still being fed through GPIO 43.

That the GPS module is powered through the pin is the explanation that fits;
it was not verified with a meter on the module. The measurements are not in
doubt.

## Method

- T-Deck Pro V1.1, battery only (USB unplugged, checked through the
  charger's VBUS bit), 4.19-4.22 V.
- Current from the BQ27220 fuel gauge: whole milliamps, averaged over about
  one second. Each reading is taken right after a 5 s light sleep, so it
  covers the sleep that just ended. Negative means discharge.
- States are alternated or repeated at the end of a run to rule out drift.
- The app's two sleep settings (RTC peripherals on, pads keep their awake
  state) are one-way for a boot, so runs that compare against the stock sleep
  start from a fresh boot with the stock sleep first.
- A pad that is held (`Pin(n, hold=True)`) through the stock sleep keeps its
  level; a pad given to `lowpower.configure()` goes high-impedance during the
  app's sleep and comes back on wake.

Two traps hit while measuring: a held SDA/SCL pad blocks the gauge (release
the holds before reading), and **holding a pad did not reproduce what the
live pad cost** for GPIO 43 (held high: 7-8 mA; live: 25 mA). Only isolating
live pads group by group in the app's own sleep found it.

## Raw readings

All values in mA, one per 5 s sleep.

### 1. E-ink booster (`tdeck_pro_panel_power_probe.py`)

Booster switched on / off three times after a real refresh.

```
ON  awake=-96 sleep=[-37, -37, -37, -37, -37, -37, -37, -37]
OFF awake=-70 sleep=[-36, -37, -36, -37, -36, -37, -36, -37]
ON  awake=-70 sleep=[-36, -37, -37, -37, -36, -37, -37, -37]
OFF awake=-67 sleep=[-37, -36, -36, -36, -37, -36, -37, -36]
ON  awake=-70 sleep=[-37, -37, -37, -36, -37, -36, -37, -37]
OFF awake=-69 sleep=[-37, -36, -37, -36, -37, -36, -36, -36]
```

Mean 36.9 on, 36.4 off: about 0.5 mA.

### 2. Stock sleep against the app's sleep (`tdeck_pro_esp_sleep_probe.py`)

Radio LDO, GPS and audio enables held at their levels throughout.

```
S1 stock lightsleep (pads isolated, RTC periph off) [-7, -7, -7, -7, -8, -7, -7, -7, -7, -7, -7, -7]
L  lowpower.sleep   (pads kept, RTC periph on)      [-40, -39, -40, -40, -40, -39, -40, -39, -40, -40, -40, -39]
S2 stock lightsleep after lowpower (inherits both)  [-40, -39, -40, -39, -40, -39, -40, -39, -40, -39, -40, -39]
```

### 3. Pads held through the stock sleep (`tdeck_pro_pad_sleep_probe.py`)

Pad levels at the start: `0=1 1=0 2=0 3=1 4=1 5=0 6=1 7=0 8=0 9=0 10=0 11=0
12=1 13=1 14=1 15=1 16=1 17=1 18=0 19=0 20=0 21=0 33=0 34=1 35=0 36=0 37=1
38=0 40=0 42=0 43=1 44=1 45=0 47=1 48=0`

Run 1, by group (two rows unreadable: the held I2C pads blocked the gauge):

```
none held                          [-7, -7, -7, -7, -7, -8]
panel+spi 36 33 47 34 35 37 16     [-7, -7, -7, -7, -7, -7]
lora 3 4 6 5                       [-7, -7, -7, -7, -7, -7]
kbd light 42, vibration 2          [-7, -7, -7, -7, -7, -7]
audio 7 8 9, mic 18 17             [-20, -20, -20, -20, -20, -20]
gps/modem/sd 44 43 1 40 10 11 48   [-8, -7, -8, -7, -8, -7]
usb 19 20                          [-7, -7, -7, -7, -7, -7]
rest 0 21 45                       [-7, -7, -7, -8, -7, -7]
none held (again)                  [-7, -7, -7, -7, -7, -7]
```

Run 2, pin by pin, holds released before each reading:

```
none held                          [-7, -8, -7, -8, -7, -8]
EVERY pad held                     [-19, -19, -19, -19, -18, -19]
all audio+mic 7 8 9 18 17          [-20, -20, -20, -20, -20, -20]
i2c bus 13 14                      [-7, -7, -7, -7, -7, -7]
kbd int 15                         [-7, -7, -7, -7, -7, -7]
touch int 12                       [-7, -7, -7, -7, -7, -7]
touch rst 38                       [-6, -6, -6, -6, -6, -6]
i2s bclk 7                         [-7, -7, -7, -7, -7, -7]
i2s dout 8                         [-7, -7, -7, -7, -7, -7]
i2s lrck 9                         [-20, -21, -20, -20, -20, -21]
mic clk 18                         [-7, -7, -7, -7, -7, -7]
mic data 17                        [-7, -7, -7, -7, -7, -7]
none held (again)                  [-7, -7, -7, -7, -7, -7]
```

### 4. RTC domain and the LRCK pin (`tdeck_pro_sleep_split_probe.py`)

An ext0 wake armed on the keyboard INT line turns the RTC peripheral domain
on while the stock sleep still isolates the pads.

```
none held                                    [-7, -7, -7, -7, -7, -7]
ext0 armed (RTC periph ON), none held        [-7, -7, -7, -7, -7, -7]
ext0 armed (RTC periph ON), EVERY pad held   [-19, -18, -19, -18, -19, -18]
ext0 off again, none held                    [-7, -7, -8, -7, -7, -7]
LRCK as INPUT, EVERY pad held                [-6, -5, -5, -5, -6, -5]
LRCK driven HIGH, held                       [-20, -21, -20, -20, -20, -21]
LRCK low held, AUDIO_EN low                  [-7, -7, -6, -7, -6, -7]
none held (again)                            [-7, -7, -7, -7, -7, -7]
```

### 5. First fix, in the app's own sleep (`tdeck_pro_sleep_fix_probe.py`, run 1)

```
A lowpower.sleep, nothing configured         [-37, -36, -37, -36, -37, -36, -37, -36, -37, -36, -37, -36]
B + flash/PSRAM standby pads                 [-36, -36, -36, -36, -36, -36, -36, -36, -36, -36, -36, -36]
C + I2S LRCK isolated (the board's setting)  [-23, -23, -23, -23, -23, -23, -23, -23, -23, -23, -23, -23]
```

### 6. Pads isolated in the app's own sleep (`tdeck_pro_sleep_groups_probe.py`)

LRCK isolated throughout. Run 1, by group:

```
baseline (LRCK only)                     [-25, -26, -25, -25, -25, -25]
panel+spi 36 33 47 34 35 37 16           [-25, -26, -25, -25, -25, -25]
lora 3 4 6 5                             [-26, -26, -26, -26, -26, -26]
i2c+ints 13 14 15 12 38                  [-27, -27, -27, -27, -27, -27]
kbd light 42 vib 2 i2s 7 8 mic 18 17     [-26, -26, -26, -26, -26, -26]
gps/modem/sd 44 43 1 40 10 11 48         [-5, -5, -5, -5, -5, -5]
usb 19 20, rest 0 21 45                  [-26, -26, -26, -26, -26, -26]
enables 46 39 41 (rails may drop)        [-23, -23, -23, -22, -23, -23]
baseline again                           [-24, -24, -24, -24, -24, -24]
```

Run 2, pin by pin:

```
baseline (LRCK only)                     [-26, -25, -25, -25, -26, -25]
gps rx 44 (uart rx)                      [-25, -25, -25, -25, -25, -25]
gps tx 43 (uart tx)                      [-5, -5, -5, -5, -5, -5]
gps pps 1                                [-25, -25, -25, -25, -25, -25]
modem pwrkey 40                          [-25, -25, -25, -25, -25, -25]
modem rxd 10                             [-25, -25, -25, -25, -25, -26]
modem txd 11                             [-25, -25, -25, -25, -25, -25]
sd cs 48                                 [-25, -25, -25, -25, -25, -25]
gps uart 44 43 together                  [-5, -4, -5, -4, -5, -4]
baseline again                           [-25, -25, -25, -25, -25, -25]
```

### 7. Final firmware, new against old in one run (`tdeck_pro_sleep_fix_probe.py`, run 2)

GPIO 43 read 0 as the board left it (parked).

```
D app config, GPS line parked (new)          [-5, -5, -5, -5, -5, -5, -5, -5, -5, -5, -5, -5]
  awake, GPS line parked                     [-51, -51, -51, -51, -51, -51, -51, -51, -51, -51]
E app config, GPS line driven high (old)     [-25, -26, -25, -26, -25, -25, -25, -25, -25, -25, -25, -25]
  awake, GPS line driven high                [-67, -72, -72, -72, -72, -72, -72, -72, -72, -72]
D again                                      [-5, -5, -5, -5, -5, -5, -5, -5, -5, -5, -5, -5]
```

## DC-DC against LDO, radio listening (`tdeck_pro_dcdc_probe.py`)

Since 2026-10-31 the Pro's urns preset runs the SX1262 regulator in DC-DC
mode (`use_dcdc`). The probe builds the radio as the app does, listens
through 12 x 5 s of the app's sleep per block, and switches the regulator
live between blocks (LDO, DC-DC, LDO again). Expected from the datasheet:
about 5 mA less in every row where the radio listens.

Run 2 (the one that completed; 4.18 V at the start):

```
L  LDO, radio listening, asleep          [-16, -15, -15, -15, -16, -15, -16, -15, -15, -15, -16, -15]
   LDO, awake                            [-67, -67, -68, -67, -68, -68, -68, -68]
D  DC-DC, radio listening, asleep        [-11, -11, -11, -11, -11, -11, -11, -11, -11, -11, -11, -11]
   DC-DC, awake                          [-34, -63, -63, -63, -64, -63, -63, -63]
L  LDO again                             [-15, -16, -15, -16, -15, -16, -15, -15, -15, -15, -15, -15]
```

So the app's sleep with the radio listening is **15-16 mA in LDO mode and
11 mA in DC-DC mode**: the 4-5 mA the datasheet gives for the regulator
(8.8 against 4.6 mA at the reset gain), on top of the 5 mA board floor and
the TCXO. Awake with the radio listening read 67 mA (LDO) and 63 mA
(DC-DC) here.

Run 1 (3.99 V at the start) wrote its LDO rows -- asleep 16 mA, awake
**30 mA** -- and then stopped without a trace at the switch to DC-DC; the
instrumented run 2 passed every step. Neither the stop nor the 30 against
67 mA awake is explained: the awake rows are taken right after a sleep
block, and something about the deck's state between the two runs (the
charge level, a packet in flight, what the first run's crash left behind)
differs. The asleep figures, which are what the deck spends its hours in,
agree between the runs.

## Not done

- Why run 1 of the DC-DC probe stopped at the regulator switch, and why its
  awake row read 30 mA against run 2's 67 mA.
- LRCK still drives the DAC while the deck is awake (13 mA); only the sleep
  releases it.
- The T-Deck v1 is unchanged. It has no fuel gauge, was not on the bench, and
  its board file asks for none of the new sleep settings.
- No external meter was used: all figures are the gauge's.
