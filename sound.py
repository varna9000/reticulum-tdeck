# T-Deck I2S Notification Tones + Voice Playback
# MAX98357A amplifier on BCK=7, WS=5, DOUT=6
# Soft tones with fade-in/fade-out envelope

import struct
import math
import micropython
from machine import Pin, I2S

# I2S config
_BCK  = 7
_WS   = 5
_DOUT = 6
_RATE = 8000  # 8kHz sample rate
_BITS = 16


@micropython.viper
def _scale_pcm(dst, src, n: int, factor: int):
    """Fixed-point attenuation of 16-bit LE signed PCM: out = in*factor>>8.
    factor 0..256 (256 = unity) — attenuation only, cannot clip."""
    d = ptr16(dst)
    s = ptr16(src)
    for i in range(n):
        x = int(s[i])
        if x >= 0x8000:
            x -= 0x10000
        x = (x * factor) >> 8
        d[i] = x & 0xFFFF


class Sound:

    def __init__(self):
        self.enabled = True
        self.volume = 8       # 0-10, default 8
        self._pcm_scratch = None  # reusable buffer for volume-scaled playback
        self._i2s = None
        self._rx_buf = None
        self._tx_buf = None
        self._ann_buf = None
        self._es7210 = None
        self._i2s_mic = None
        self._mclk = None
        self._recording = False

    def init(self):
        """Initialize I2S and pre-compute tone buffers."""
        self._i2s = I2S(
            0,
            sck=Pin(_BCK),
            ws=Pin(_WS),
            sd=Pin(_DOUT),
            mode=I2S.TX,
            bits=_BITS,
            format=I2S.MONO,
            rate=_RATE,
            ibuf=16384,  # 1s buffer — prevents underrun during C driver GIL holds
        )
        self._regen_tones()

    def _regen_tones(self):
        """(Re)build the notification tone buffers at the current volume."""
        v = self.volume
        # RX: gentle two-tone chirp (soft ding)
        self._rx_buf = self._gen_chirp(660, 880, 120, 4000 * v // 10)
        # TX: short soft blip
        self._tx_buf = self._gen_tone(440, 80, 3000 * v // 10)
        # Announce: short rising chirp
        self._ann_buf = self._gen_chirp(440, 660, 100, 3000 * v // 10)

    def set_volume(self, v):
        """Set playback volume 0-10 (0 = mute, 10 = full) and rebuild tones."""
        v = 0 if v < 0 else (10 if v > 10 else int(v))
        self.volume = v
        if self._i2s:
            self._regen_tones()

    @staticmethod
    def _gen_tone(freq, duration_ms, amplitude=4000):
        """Generate PCM buffer with fade-in/fade-out envelope."""
        n = _RATE * duration_ms // 1000
        fade = n // 4  # 25% fade in/out
        buf = bytearray(n * 2)
        for i in range(n):
            # Envelope: fade in, sustain, fade out
            if i < fade:
                env = i / fade
            elif i > n - fade:
                env = (n - i) / fade
            else:
                env = 1.0
            val = int(amplitude * env * math.sin(2 * math.pi * freq * i / _RATE))
            struct.pack_into("<h", buf, i * 2, val)
        return buf

    @staticmethod
    def _gen_chirp(f_start, f_end, duration_ms, amplitude=4000):
        """Generate a frequency sweep with fade envelope."""
        n = _RATE * duration_ms // 1000
        fade = n // 4
        buf = bytearray(n * 2)
        for i in range(n):
            t = i / n
            # Envelope
            if i < fade:
                env = i / fade
            elif i > n - fade:
                env = (n - i) / fade
            else:
                env = 1.0
            # Linear frequency sweep
            freq = f_start + (f_end - f_start) * t
            val = int(amplitude * env * math.sin(2 * math.pi * freq * i / _RATE))
            struct.pack_into("<h", buf, i * 2, val)
        return buf

    def play_rx(self):
        """Play incoming message notification."""
        if self.enabled and self._i2s and self._rx_buf:
            self._i2s.write(self._rx_buf)

    def play_tx(self):
        """Play outgoing message notification."""
        if self.enabled and self._i2s and self._tx_buf:
            self._i2s.write(self._tx_buf)

    def play_announce(self):
        """Play announce notification."""
        if self.enabled and self._i2s and self._ann_buf:
            self._i2s.write(self._ann_buf)

    def play_pcm(self, pcm_bytes):
        """Play raw 16-bit LE signed PCM at 8kHz mono, attenuated by volume."""
        if not (self.enabled and self._i2s and pcm_bytes) or self.volume == 0:
            return
        if self.volume >= 10:
            self._i2s.write(pcm_bytes)
            return
        n = len(pcm_bytes)
        if self._pcm_scratch is None or len(self._pcm_scratch) < n:
            self._pcm_scratch = bytearray(n)
        mv = memoryview(self._pcm_scratch)[:n]
        _scale_pcm(mv, pcm_bytes, n // 2, self.volume * 256 // 10)
        self._i2s.write(mv)

    def init_mic(self, i2c):
        """Initialize ES7210 mic ADC, MCLK, and I2S(1) RX.
        I2S stays alive across recordings to avoid ESP32-S3 TDM bit-order
        reinit bug (micropython#11245). Thread shutdown uses _recording flag."""
        from machine import PWM
        self._mclk = PWM(Pin(48), freq=4096000, duty_u16=32768)

        from es7210 import ES7210
        self._es7210 = ES7210(i2c)
        self._es7210.init(gain=ES7210.GAIN_37_5DB)

        self._i2s_mic = I2S(
            1, sck=Pin(47), ws=Pin(21), sd=Pin(14),
            mode=I2S.RX, bits=16, format=I2S.STEREO,
            rate=16000, ibuf=65536,
        )
        self._mic_dc = 0  # running DC offset estimate
        self._mic_raw = None   # pre-allocated read buffer (set in start_recording)
        self._mic_out = None   # pre-allocated output buffer
        self._mic_peak = 0     # |peak| of the most recent chunk (for a VU meter)
        self._capturing = False  # False during the warm-up, True once capturing

    @property
    def is_capturing(self):
        """True once the mic thread is actually storing audio. The recording
        screen shows 'Warming mic...' until this flips (normally ~0.2s; the
        full warm-up only runs in the rare re-warm fallback)."""
        return self._capturing

    def _kick_mic(self):
        """ES7210 clock stop->start — makes a warmed ADC begin outputting."""
        import time
        self._es7210.stop()
        time.sleep_ms(10)
        self._es7210.start()
        time.sleep_ms(100)

    @staticmethod
    def _raw_spread(raw):
        """Raw left-channel min..max spread of a chunk. A live ADC at 37.5dB
        gain always shows a noise floor (measured >=196 even in a quiet room);
        a dead one reads an exact constant -> spread 0. Samples every 4th
        left slot — plenty to distinguish noise from a constant, 4x faster."""
        mn = 32767
        mx = -32768
        for i in range(0, len(raw), 32):
            v = struct.unpack_from("<h", raw, i)[0]
            if v < mn: mn = v
            if v > mx: mx = v
        return mx - mn

    def prime_mic(self, abort=None):
        """Bring the ES7210 to a live, producing state and LEAVE it running.

        Measured behaviour: from cold the ADC produces audio only after ~3s
        of ACTIVE reads followed by a clock stop->start kick — but once
        producing it stays live indefinitely while the clock keeps running
        (stopping the clock resets it within ~5s). So: prime once at boot
        (~4s, behind the splash), never stop the clock again (see
        stop_recording), and every capture-time call is just a fast stale-
        ring flush + liveness check (~0.6s).

        Deliberately NO per-capture clock kick: post-kick resync is
        nondeterministic (measured 0.1s..7s+ under identical conditions),
        which caused silent recording starts and long waits — the original
        driver's per-recording start() had exactly that flakiness. A
        free-running ADC keeps one sync state for days, so recordings also
        sound consistent with each other.

        abort: optional callable polled during the wait/slow paths; return
        True to abandon (recording cancelled). Returns True when live."""
        if not self._i2s_mic or not self._es7210:
            return True
        import time
        raw = bytearray(640 * 8)
        self._es7210.start()
        # Drain the stale DMA ring (13 x 5120B > the 64KB ring, so the
        # recording starts within ~0.2s of "now"). The sleep between reads
        # yields the GIL so the display (core 0) can paint the recording
        # screen while we flush — without it the paint visibly crawls.
        for _ in range(13):
            self._i2s_mic.readinto(raw)
            time.sleep_ms(3)
        if self._raw_spread(raw) > 0:
            return True
        # Not producing. Give a transient state a moment before the full
        # re-warm (fresh chunks only — each read is ~80ms of new audio).
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < 2000:
            if abort and abort():
                return False
            self._i2s_mic.readinto(raw)
            if self._raw_spread(raw) > 0:
                return True
        # Cold (boot) or wedged: active reads + kick; retry a few rounds.
        for _ in range(3):
            t0 = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), t0) < 3000:
                if abort and abort():
                    return False
                self._i2s_mic.readinto(raw)
            self._kick_mic()
            self._i2s_mic.readinto(raw)   # settle chunk
            self._i2s_mic.readinto(raw)   # fresh post-kick sample
            if self._raw_spread(raw) > 0:
                return True
        return False

    def start_recording(self, chunk_samples=640):
        """Allocate capture buffers and arm recording. The capture thread
        calls prime_mic() before storing — normally a ~0.2s no-op since the
        ADC was primed at boot and its clock is never stopped."""
        self._mic_raw = bytearray(chunk_samples * 8)
        self._mic_out = bytearray(chunk_samples * 2)
        self._mic_dc = 0  # reset DC offset estimator
        self._mic_peak = 0
        self._capturing = False
        self._recording = True

    def stop_recording(self):
        """Stop mic recording. The ES7210 clock is deliberately left RUNNING:
        a kicked ADC stays live only while clocked — stopping it here would
        force the full ~3s warm-up on the next recording (see prime_mic)."""
        self._recording = False
        self._capturing = False
        import time; time.sleep_ms(200)  # wait for mic thread to see flag and exit
        self._mic_raw = None
        self._mic_out = None

    @property
    def is_recording(self):
        return self._recording

    def read_mic_chunk(self, out_samples=160):
        """Read 16kHz stereo, extract non-zero left channel samples.
        Pattern is [L,R,0,0] repeating — valid L at stride 4.
        Applies DC offset removal. Returns pre-allocated 8kHz mono 16-bit PCM.
        Uses pre-allocated buffers to avoid GC pauses during recording."""
        if not self._i2s_mic:
            return None
        # Use pre-allocated buffers if available, else allocate (standalone use)
        raw = self._mic_raw if self._mic_raw else bytearray(out_samples * 8)
        out = self._mic_out if self._mic_out else bytearray(out_samples * 2)
        self._i2s_mic.readinto(raw)
        dc = self._mic_dc
        for i in range(out_samples):
            # Valid left samples at stride 8 bytes (every other stereo pair)
            idx = i * 8
            s = struct.unpack_from("<h", raw, idx)[0]
            # IIR DC removal: dc tracks slowly, subtract from signal
            dc = (dc * 31 + s) >> 5
            s = s - dc
            if s > 32767: s = 32767
            elif s < -32768: s = -32768
            struct.pack_into("<h", out, i * 2, s)
        self._mic_dc = dc
        return out

    def deinit(self):
        if self._i2s:
            self._i2s.deinit()
            self._i2s = None
