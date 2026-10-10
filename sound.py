# T-Deck I2S Notification Tones + Voice Playback
# Soft tones with fade-in/fade-out envelope.
#
# The I2S pins come from the board (board.SPK_PINS): the v1's MAX98357A sits
# on BCK=7, WS=5, DOUT=6, the Pro's PCM5102A on BCLK=7, LRCK=9, DOUT=8. The
# v1 numbers stay as init()'s defaults, so nothing that already calls init()
# bare changes behaviour.

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


try:
    ptr16                     # a viper type name, resolved by the compiler
except NameError:
    # Host tests: 16-bit word indexing over the buffer, which is what viper's
    # ptr16 does on the device. Never reached there.
    ptr16 = lambda b: memoryview(b).cast("H")


@micropython.viper
def _pdm_to_8k(dst, src, n: int, dc: int) -> int:
    """Decimate 16 kHz mono PCM (2n samples in src) to 8 kHz (n samples in
    dst) with a 2-tap average and IIR DC removal. Returns the DC estimate
    in the top 16 bits and the chunk's |peak| in the low 16."""
    d = ptr16(dst)
    s = ptr16(src)
    pk = 0
    for i in range(n):
        a = int(s[2 * i])
        b = int(s[2 * i + 1])
        if a >= 0x8000:
            a -= 0x10000
        if b >= 0x8000:
            b -= 0x10000
        x = (a + b) >> 1
        dc = (dc * 31 + x) >> 5
        x -= dc
        if x > 32767:
            x = 32767
        elif x < -32768:
            x = -32768
        d[i] = x & 0xFFFF
        if x < 0:
            x = -x
        if x > pk:
            pk = x
    return ((dc & 0xFFFF) << 16) | pk


@micropython.viper
def _scale_pcm(dst, src, n: int, factor: int):
    """Fixed-point scaling of 16-bit LE signed PCM: out = in*factor>>8,
    saturated. factor 256 = unity; below attenuates, above (a board gain)
    amplifies and clips at full scale rather than wrapping."""
    d = ptr16(dst)
    s = ptr16(src)
    for i in range(n):
        x = int(s[i])
        if x >= 0x8000:
            x -= 0x10000
        x = (x * factor) >> 8
        if x > 32767:
            x = 32767
        elif x < -32768:
            x = -32768
        d[i] = x & 0xFFFF


class Sound:

    def __init__(self):
        self.enabled = True
        self.volume = 8       # 0-10, default 8
        self._pcm_scratch = None  # reusable buffer for volume-scaled playback
        self._i2s = None
        self.gain = 1
        self.pitch = 1
        self._rx_buf = None
        self._tx_buf = None
        self._ann_buf = None
        self._es7210 = None
        self._i2s_mic = None
        self._pdm = None          # the pdm_mic module, once init_pdm_mic() ran
        self._mclk = None
        self._recording = False

    def init(self, bck=_BCK, ws=_WS, dout=_DOUT, gain=1, pitch=1, port=0):
        """Initialize I2S on the given pins and pre-compute tone buffers.

        gain multiplies every level (tones and PCM playback) and pitch every
        tone frequency; both come from the board (board.AUDIO_GAIN/PITCH).
        The v1's MAX98357A amplifier takes the tones as written. The Pro's
        PCM5102A feeds a small speaker that is near-silent at the stock
        440-660 Hz chirps and level: it needs them an octave up and several
        times louder (heard on hardware, 2026-10-09).
        """
        self.gain = gain
        self.pitch = pitch
        # port: the Pro plays on I2S 1, because its PDM microphone needs
        # port 0 (the S3's only PDM receiver); the v1 plays on 0.
        self._i2s = I2S(
            port,
            sck=Pin(bck),
            ws=Pin(ws),
            sd=Pin(dout),
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
        g = self.gain
        p = self.pitch
        # Levels are per-board (gain) and never past full scale.
        def amp(a):
            return min(30000, a * g * v // 10)
        # RX: gentle two-tone chirp (soft ding)
        self._rx_buf = self._gen_chirp(660 * p, 880 * p, 120, amp(4000))
        # TX: short soft blip
        self._tx_buf = self._gen_tone(440 * p, 80, amp(3000))
        # Announce: short rising chirp
        self._ann_buf = self._gen_chirp(440 * p, 660 * p, 100, amp(3000))

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
        if self.volume >= 10 and self.gain == 1:
            self._i2s.write(pcm_bytes)
            return
        n = len(pcm_bytes)
        if self._pcm_scratch is None or len(self._pcm_scratch) < n:
            self._pcm_scratch = bytearray(n)
        mv = memoryview(self._pcm_scratch)[:n]
        _scale_pcm(mv, pcm_bytes, n // 2, self.volume * 256 * self.gain // 10)
        self._i2s.write(mv)

    def init_mic(self, i2c):
        """Initialize ES7210 mic ADC, MCLK, and I2S(1) RX.
        I2S stays alive across recordings to avoid ESP32-S3 TDM bit-order
        reinit bug (micropython#11245). Thread shutdown uses _recording flag."""
        from es7210 import ES7210
        self._mclk = None
        self._mclk_pin = 48
        self._mic_gain = ES7210.GAIN_37_5DB
        self._start_mclk()

        self._es7210 = ES7210(i2c)
        self._es7210.init(gain=self._mic_gain)

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

    def init_pdm_mic(self, clk, data):
        """Initialize a PDM microphone (T-Deck Pro: MIC_CLOCK 18, MIC_DATA 17)
        through the pdm_mic built-in module. The hardware filter delivers 16-bit
        mono PCM at 16 kHz; read_mic_chunk() halves that to the 8 kHz Codec2
        wants. No ADC to prime: a PDM mic is live as soon as it is clocked."""
        import pdm_mic
        pdm_mic.init(clk, data, 16000)
        self._pdm = pdm_mic
        self._mic_dc = 0
        self._mic_raw = None
        self._mic_out = None
        self._mic_peak = 0
        self._capturing = False

    @property
    def is_capturing(self):
        """True once the mic thread is actually storing audio. The recording
        screen shows 'Warming mic...' until this flips (normally ~0.2s; the
        full warm-up only runs in the rare re-warm fallback)."""
        return self._capturing

    # ES7210 recovery after its clocks stopped (light sleep). Bench
    # 2026-10-10 on the v1: a kick alone never brought it back; MCLK restart
    # + re-init + polling with a kick every MIC_KICK_MS recovered it in
    # 0.4-7 s, 4 of 4 (the ADC resyncs at a random moment after a kick, so
    # kicks are re-rolls and polling catches the first live chunk).
    MIC_KICK_MS = 1500      # between kicks while polling for life
    MIC_ROUND_MS = 12000    # after this long, start over with a fresh re-init
    MIC_RECOVER_MS = 36000  # total bound: three rounds, then give up

    def _start_mclk(self):
        """(Re)create the 4.096 MHz master clock. The LEDC output does not
        feed the ADC usefully again after a light sleep until it is rebuilt;
        rebuilding it is cheap and is the first step of every recovery."""
        from machine import PWM
        if self._mclk is not None:
            self._mclk.deinit()
        self._mclk = PWM(Pin(self._mclk_pin), freq=4096000, duty_u16=32768)

    def _recover_es7210(self, raw, abort=None):
        """Bring a dead (clock-starved) ADC back: restart MCLK, re-init the
        ADC, then read continuously until a chunk shows a noise floor, kicking
        the ADC clock every MIC_KICK_MS. A round that stays dead for
        MIC_ROUND_MS is restarted from the re-init; MIC_RECOVER_MS bounds the
        whole thing. abort() is polled every chunk."""
        import time
        t_all = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t_all) < self.MIC_RECOVER_MS:
            self._start_mclk()
            self._es7210.init(gain=self._mic_gain)
            t_round = time.ticks_ms()
            t_kick = t_round
            while time.ticks_diff(time.ticks_ms(), t_round) < self.MIC_ROUND_MS:
                if abort and abort():
                    return False
                self._i2s_mic.readinto(raw)
                if self._raw_spread(raw) > 0:
                    return True
                now = time.ticks_ms()
                if time.ticks_diff(now, t_kick) >= self.MIC_KICK_MS:
                    self._kick_mic()
                    t_kick = time.ticks_ms()
                if time.ticks_diff(now, t_all) >= self.MIC_RECOVER_MS:
                    return False
        return False

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
        if self._pdm is not None:
            # Drop whatever sat in the DMA ring before the user pressed record
            # (up to ~250 ms of stale audio), then capture. Nothing to warm.
            raw = self._mic_raw if self._mic_raw else bytearray(4096)
            self._pdm.readinto(raw)
            return True
        return self._prime_es7210(abort)

    def _prime_es7210(self, abort=None):
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

        A light sleep stops MCLK and the I2S clocks, after which the ADC
        reads dead; see _recover_es7210 for what brings it back and how
        long that takes.

        abort: optional callable polled during the slow path; return True
        to abandon (recording cancelled). Returns True when live."""
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
        # Not producing: the ADC lost its clocks (light sleep) or is cold.
        return self._recover_es7210(raw, abort)

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
        if self._pdm is not None:
            return self._read_pdm_chunk(out_samples)
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

    def _read_pdm_chunk(self, out_samples):
        """16 kHz mono from the PDM filter -> 8 kHz mono: average each pair
        of samples (a 2-tap low-pass, enough for speech), remove DC. The
        pre-allocated buffers are sized for the ES7210 path (8 B per output
        sample); this path needs 4 B, so it reads into a slice."""
        raw = self._mic_raw if self._mic_raw else bytearray(out_samples * 4)
        out = self._mic_out if self._mic_out else bytearray(out_samples * 2)
        need = out_samples * 4
        mv = memoryview(raw)[:need]
        got = 0
        while got < need:
            n = self._pdm.readinto(mv[got:])
            if not n:
                return None
            got += n
        packed = _pdm_to_8k(out, raw, out_samples, self._mic_dc)
        self._mic_dc = (packed >> 16) if (packed >> 16) < 0x8000 else (packed >> 16) - 0x10000
        self._mic_peak = packed & 0xFFFF
        return out

    def deinit(self):
        if self._i2s:
            self._i2s.deinit()
            self._i2s = None
