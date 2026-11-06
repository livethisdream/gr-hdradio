#
# Copyright 2026 Neil Rogers.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""HD Radio Receiver: any sample rate in, HD Radio Decoder behind it."""

import math
from fractions import Fraction

from gnuradio import blocks, filter, gr

from .hd_decoder import MESSAGE_PORTS, hd_decoder
from .radio import NATIVE_RATE

# Largest interpolation and decimation a Rational Resampler is used for; a
# ratio with bigger terms goes to the polyphase arbitrary resampler instead.
_MAX_INTERP = 64
_MAX_DECIM = 512


def plan_resampler(samp_rate, mode="fm"):
    """How the receiver gets from samp_rate to nrsc5's native rate.

    Returns ("none", 1, 1), ("rational", interp, decim), or
    ("arbitrary", rate, None).
    """
    native = Fraction(NATIVE_RATE[mode]).limit_denominator(1 << 20)
    ratio = native / Fraction(samp_rate).limit_denominator(1 << 20)
    if ratio == 1:
        return ("none", 1, 1)
    if ratio.numerator <= _MAX_INTERP and ratio.denominator <= _MAX_DECIM:
        return ("rational", ratio.numerator, ratio.denominator)
    return ("arbitrary", float(ratio), None)


class hd_receiver(gr.hier_block2):
    """HD Radio (NRSC-5) receiver for complex samples at any rate.

    It shifts the station to 0 Hz by `offset` (the station's frequency minus
    the radio's center frequency), resamples from `samp_rate` to nrsc5's
    native rate, and decodes. A PlutoSDR at 1,488,375 S/s takes an exact
    decimate-by-2; a rate such as 2 MS/s goes through a polyphase arbitrary
    resampler. Outputs and message ports are HD Radio Decoder's.
    """

    def __init__(self, samp_rate=1488375, mode="fm", offset=0.0,
                 programs=(0,), stereo=True, paced=True, preroll=0.5):
        mode = str(mode).lower()
        programs = [int(p) for p in programs]
        n_out = len(programs) * (2 if stereo else 1)
        gr.hier_block2.__init__(
            self, "hd_receiver",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(n_out, n_out, gr.sizeof_float) if n_out
            else gr.io_signature(0, 0, 0))

        self.samp_rate = float(samp_rate)
        self.mode = mode
        native = NATIVE_RATE[mode]
        if native > self.samp_rate * 1.0001:
            # Upsampling can't create the sidebands that were never sampled.
            gr.log.warn(f"hd_receiver: samp_rate {self.samp_rate:g} is below "
                        f"nrsc5's {native:g} S/s; the HD sidebands are cut off")

        self.rotator = blocks.rotator_cc(0.0)
        self.set_offset(offset)
        self.plan = plan_resampler(self.samp_rate, mode)
        kind, a, b = self.plan
        # Both resamplers' default filters pass the HD signal (about +/-200 kHz
        # FM, +/-15 kHz AM) and reject what would alias into it.
        if kind == "rational":
            self.resampler = filter.rational_resampler_ccc(a, b)
        elif kind == "arbitrary":
            self.resampler = filter.pfb.arb_resampler_ccf(a, taps=None,
                                                          flt_size=32,
                                                          atten=80)
        else:
            self.resampler = None

        self.decoder = hd_decoder(mode, programs, stereo, paced, preroll)

        chain = [self, self.rotator]
        if self.resampler is not None:
            chain.append(self.resampler)
        chain.append(self.decoder)
        self.connect(*chain)
        for i in range(n_out):
            self.connect((self.decoder, i), (self, i))
        for port in MESSAGE_PORTS:
            self.message_port_register_hier_out(port)
            self.msg_connect(self.decoder, port, self, port)

    def set_offset(self, offset):
        """Shift so the station at `offset` Hz from center lands on 0 Hz."""
        self.offset = float(offset)
        self.rotator.set_phase_inc(-2 * math.pi * self.offset / self.samp_rate)
