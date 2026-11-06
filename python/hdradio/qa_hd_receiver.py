#
# Copyright 2026 Neil Rogers.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""HD Radio Receiver end to end on a real capture, with libnrsc5.

Set HDRADIO_SAMPLE to a raw cu8 capture at 1,488,375 S/s, such as nrsc5's
support/sample (KUT 90.5, Austin), unpacked with `xz -d`. Without it the
end-to-end tests are skipped; the resampler plan tests always run.
"""

import os
import unittest

import numpy as np
import pmt
from gnuradio import blocks, gr, gr_unittest

try:
    from gnuradio.hdradio import hd_receiver, plan_resampler
    from gnuradio.hdradio.hd_decoder import MESSAGE_PORTS
except ImportError:  # running from the source tree
    from hdradio import hd_receiver, plan_resampler
    from hdradio.hd_decoder import MESSAGE_PORTS

SAMPLE = os.environ.get("HDRADIO_SAMPLE")


def cu8_to_complex(path):
    u = np.fromfile(path, dtype=np.uint8).astype(np.float32)
    return ((u - 127.5) / 128).view(np.complex64)


class qa_hd_receiver(gr_unittest.TestCase):

    def test_plan(self):
        self.assertEqual(plan_resampler(744187.5), ("none", 1, 1))
        self.assertEqual(plan_resampler(1488375), ("rational", 1, 2))
        self.assertEqual(plan_resampler(2976750), ("rational", 1, 4))
        self.assertEqual(plan_resampler(1488375, "am"), ("rational", 1, 32))
        kind, rate, _ = plan_resampler(2e6)
        self.assertEqual(kind, "arbitrary")
        self.assertAlmostEqual(rate, 744187.5 / 2e6)

    def decode(self, samples, samp_rate, **kw):
        tb = gr.top_block()
        rx = hd_receiver(samp_rate, "fm", **kw)
        tb.connect(blocks.vector_source_c(samples), rx)
        n_out = len(kw.get("programs", [0])) * (2 if kw.get("stereo", True)
                                                else 1)
        sinks = [blocks.vector_sink_f() for _ in range(n_out)]
        for i, s in enumerate(sinks):
            tb.connect((rx, i), s)
        debug = {p: blocks.message_debug() for p in MESSAGE_PORTS}
        for p, d in debug.items():
            tb.msg_connect(rx, p, d, "store")
        tb.run()
        msgs = {p: [pmt.to_python(d.get_message(i))
                    for i in range(d.num_messages())]
                for p, d in debug.items()}
        return [np.array(s.data()) for s in sinks], sinks, msgs

    @unittest.skipUnless(SAMPLE, "HDRADIO_SAMPLE not set")
    def test_sample_two_programs(self):
        x = cu8_to_complex(SAMPLE)
        audio, sinks, msgs = self.decode(x, 1488375, programs=[0, 1],
                                         paced=False)
        self.assertEqual(len(audio), 4)
        for a in audio:
            self.assertGreater(len(a), 10 * 44100)
            self.assertGreater(np.sqrt(np.mean(a ** 2)), 0.05)
        names = [m["name"] for m in msgs["station"]
                 if m["event"] == "station_name"]
        self.assertIn("KUT", names[0])
        self.assertTrue(any(m["event"] == "sync" for m in msgs["status"]))
        tags = sinks[0].tags()
        self.assertEqual(pmt.to_python(tags[0].value)["event"], "id3")

    @unittest.skipUnless(SAMPLE, "HDRADIO_SAMPLE not set")
    def test_sample_paced_mono(self):
        x = cu8_to_complex(SAMPLE)
        audio, _, _ = self.decode(x, 1488375, programs=[1], stereo=False,
                                  paced=True)
        self.assertEqual(len(audio), 1)
        # 44,100 audio samples for every 1,488,375 input samples, give or
        # take the few the resampler's filter holds back at the end.
        self.assertAlmostEqual(len(audio[0]), len(x) * 4 // 135, delta=4)
        self.assertGreater(np.count_nonzero(audio[0]), 8 * 44100)

    @unittest.skipUnless(SAMPLE, "HDRADIO_SAMPLE not set")
    def test_sample_metadata_only(self):
        x = cu8_to_complex(SAMPLE)
        _, _, msgs = self.decode(x, 1488375, programs=[])
        self.assertTrue(msgs["id3"])
        self.assertTrue(msgs["hdc"])


if __name__ == "__main__":
    gr_unittest.run(qa_hd_receiver)
