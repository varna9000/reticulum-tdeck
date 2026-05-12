"""Record mic, encode codec2 3200, save .c2 file, decode, play back."""
import gc, time, struct
gc.collect()

from machine import Pin, SoftI2C
from sound import Sound

sound = Sound()
sound.init()
_mic_i2c = SoftI2C(scl=Pin(8), sda=Pin(18), freq=100000)
sound.init_mic(_mic_i2c)

import codec2_fast_xtensawin as c2

print("=== Codec2 Roundtrip Test ===")
print("Recording 10 seconds... speak now!")
sound.start_recording()

rec = bytearray(10 * 8000 * 2)  # 10s at 8kHz 16-bit
pos = 0
t0 = time.ticks_ms()
while pos < len(rec) - 320:
    chunk = sound.read_mic_chunk(160)
    if chunk:
        rec[pos:pos+320] = chunk
        pos += 320

sound.stop_recording()
print("Recorded", pos, "bytes in", time.ticks_diff(time.ticks_ms(), t0), "ms")

n = pos // 2
# DC removal
dc_sum = 0
for i in range(n):
    dc_sum += struct.unpack_from("<h", rec, i * 2)[0]
dc = dc_sum // n

mn, mx = 32767, -32767
for i in range(n):
    s = struct.unpack_from("<h", rec, i * 2)[0] - dc
    if s > 32767: s = 32767
    if s < -32768: s = -32768
    struct.pack_into("<h", rec, i * 2, s)
    if s < mn: mn = s
    if s > mx: mx = s
print("DC offset: %d, after removal min/max: %d / %d" % (dc, mn, mx))

# Play RAW
print("\n--- Playing RAW ---")
sound.play_tx(); time.sleep_ms(200)
i = 0
while i < pos:
    sound.play_pcm(rec[i:i+1600])
    i += 1600
time.sleep_ms(500)

# Encode
gc.collect()
pcm = bytes(rec[:pos])
del rec
print("\nEncoding mode 3200...")
t0 = time.ticks_ms()
encoded = c2.encode(pcm, 0)
print("Encoded: %d B in %d ms" % (len(encoded), time.ticks_diff(time.ticks_ms(), t0)))

# Save encoded to file for transfer/analysis
with open("last_audio.c2", "wb") as f:
    f.write(encoded)
print("Saved to last_audio.c2")

# Decode
gc.collect()
decoded = c2.decode(encoded, 0, 1)
mn2, mx2 = 32767, -32767
for i in range(0, len(decoded), 2):
    s = struct.unpack_from("<h", decoded, i)[0]
    if s < mn2: mn2 = s
    if s > mx2: mx2 = s
print("Decoded min/max: %d / %d" % (mn2, mx2))

# Play decoded without amp
print("\n--- Playing DECODED (no amp) ---")
sound.play_tx(); time.sleep_ms(200)
i = 0
while i < len(decoded):
    sound.play_pcm(decoded[i:i+1600])
    i += 1600
time.sleep_ms(500)

# Play decoded with amp
gc.collect()
print("\n--- Playing DECODED (amplified) ---")
sound.play_tx(); time.sleep_ms(200)
i = 0
while i < len(decoded):
    seg = sound.amplify_pcm(decoded[i:i+1600])
    if seg:
        sound.play_pcm(seg)
    i += 1600

print("\nDone!")
