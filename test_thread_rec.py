"""Test threaded mic recording — simulates tdeck_node's _mic_thread."""
import gc, time, struct, _thread
gc.collect()

from machine import Pin, SoftI2C
from sound import Sound

sound = Sound()
sound.init()
_mic_i2c = SoftI2C(scl=Pin(8), sda=Pin(18), freq=100000)
sound.init_mic(_mic_i2c)

_REC_CHUNK = 160
rec_buf = bytearray(10 * 8000 * 2)  # 10s
rec_pos = 0
done = False

def mic_thread():
    global rec_pos, done
    chunk_bytes = _REC_CHUNK * 2
    try:
        while sound.is_recording:
            buf = rec_buf
            if buf is None or rec_pos >= len(buf) - chunk_bytes:
                break
            chunk = sound.read_mic_chunk(_REC_CHUNK)
            if chunk and buf is rec_buf:
                buf[rec_pos:rec_pos + chunk_bytes] = chunk
                rec_pos += chunk_bytes
    except Exception as e:
        print("Thread error:", e)
    done = True

print("=== Threaded Recording Test ===")
print("Recording 5 seconds... speak now!")
sound.start_recording()
_thread.start_new_thread(mic_thread, ())

# Main thread: simulate event loop work (keyboard poll, LoRa-like delays)
t0 = time.ticks_ms()
polls = 0
while not done and time.ticks_diff(time.ticks_ms(), t0) < 5500:
    time.sleep_ms(20)  # simulate kbd poll interval
    polls += 1
    if polls % 50 == 0:  # every ~1s, simulate heavier work
        time.sleep_ms(50)

sound.stop_recording()
time.sleep_ms(100)  # let thread finish

print("Recorded %d bytes in %d ms (%d main-thread polls)" % (
    rec_pos, time.ticks_diff(time.ticks_ms(), t0), polls))

# Check quality
n = rec_pos // 2
mn, mx, nz = 32767, -32768, 0
for i in range(n):
    s = struct.unpack_from("<h", rec_buf, i * 2)[0]
    if s != 0: nz += 1
    if s < mn: mn = s
    if s > mx: mx = s
print("min=%d max=%d nonzero=%.1f%%" % (mn, mx, 100*nz/max(n,1)))

# Encode and save
import codec2_fast_xtensawin as c2
pcm = bytes(rec_buf[:rec_pos])
del rec_buf
gc.collect()
print("Encoding...")
encoded = c2.encode(pcm, 0)
with open("thread_audio.c2", "wb") as f:
    f.write(encoded)
print("Saved thread_audio.c2 (%d B)" % len(encoded))

# Play raw
print("\n--- Playing RAW ---")
sound.play_tx(); time.sleep_ms(200)
i = 0
while i < len(pcm):
    sound.play_pcm(pcm[i:i+1600])
    i += 1600
time.sleep_ms(500)

# Decode and play
gc.collect()
decoded = c2.decode(encoded, 0, 1)
print("--- Playing DECODED ---")
sound.play_tx(); time.sleep_ms(200)
i = 0
while i < len(decoded):
    sound.play_pcm(decoded[i:i+1600])
    i += 1600

print("\nDone!")
