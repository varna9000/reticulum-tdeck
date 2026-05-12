# T-Deck I2S Notification Tones + Voice Playback
# MAX98357A amplifier on BCK=7, WS=5, DOUT=6
# Soft tones with fade-in/fade-out envelope

import struct
import math
from machine import Pin, I2S

# I2S config
_BCK  = 7
_WS   = 5
_DOUT = 6
_RATE = 8000  # 8kHz sample rate
_BITS = 16


class Sound:

    def __init__(self):
        self.enabled = True
        self.volume = 8       # 0-10, default 8
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
            ibuf=4096,
        )
        # RX: gentle two-tone chirp (soft ding)
        self._rx_buf = self._gen_chirp(660, 880, 120, 4000)
        # TX: short soft blip
        self._tx_buf = self._gen_tone(440, 80, 3000)
        # Announce: short rising chirp
        self._ann_buf = self._gen_chirp(440, 660, 100, 3000)

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

    def amplify_pcm(self, pcm_bytes):
        """Amplify 16-bit LE signed PCM by volume level. Returns new buffer."""
        if self.volume == 0:
            return None
        # Volume gain: 0=mute, 5=1x, 10=4x
        gain = self.volume / 5.0  # 0..2.0
        gain *= 4.0               # 0..8.0 — Codec2 output is typically quiet
        n = len(pcm_bytes) // 2
        out = bytearray(len(pcm_bytes))
        for i in range(n):
            sample = struct.unpack_from("<h", pcm_bytes, i * 2)[0]
            sample = int(sample * gain)
            # Clamp to 16-bit range
            if sample > 32767:
                sample = 32767
            elif sample < -32767:
                sample = -32767
            struct.pack_into("<h", out, i * 2, sample)
        return out

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
        """Play raw 16-bit LE signed PCM at 8kHz mono."""
        if self.enabled and self._i2s and pcm_bytes:
            self._i2s.write(pcm_bytes)

    def init_mic(self, i2c):
        """Initialize ES7210 mic ADC and I2S(1) for recording.
        ES7210 at 16kHz stereo (ADC1=left=mic). MicroPython I2S produces
        [L,R,0,0] pattern — extract valid left samples for 8kHz Codec2."""
        from machine import PWM
        self._mclk = PWM(Pin(48), freq=4096000, duty_u16=32768)

        from es7210 import ES7210
        self._es7210 = ES7210(i2c)
        self._es7210.init(gain=ES7210.GAIN_37_5DB)

        self._i2s_mic = I2S(
            1,
            sck=Pin(47),
            ws=Pin(21),
            sd=Pin(14),
            mode=I2S.RX,
            bits=16,
            format=I2S.STEREO,
            rate=16000,
            ibuf=16384,
        )
        self._mic_dc = 0  # running DC offset estimate

    def start_recording(self):
        """Start mic recording."""
        if self._es7210:
            self._es7210.start()
        self._recording = True

    def stop_recording(self):
        """Stop mic recording."""
        self._recording = False
        if self._es7210:
            self._es7210.stop()

    @property
    def is_recording(self):
        return self._recording

    def read_mic_chunk(self, out_samples=160):
        """Read 16kHz stereo, extract non-zero left channel samples.
        Pattern is [L,R,0,0] repeating — valid L at stride 4.
        Applies DC offset removal. Returns 8kHz mono 16-bit PCM."""
        if not self._i2s_mic:
            return None
        # Need out_samples valid L samples; pattern is every other pair valid
        # So read 2x stereo pairs: out_samples * 2 pairs * 4 bytes
        raw = bytearray(out_samples * 8)
        self._i2s_mic.readinto(raw)
        out = bytearray(out_samples * 2)
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
