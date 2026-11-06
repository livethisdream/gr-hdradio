#
# Copyright 2026 Neil Rogers.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Decoder logic with a stand-in for libnrsc5: pacing, preroll, dropouts,
burst output, tags, and how each nrsc5 event becomes a message."""

import datetime

import numpy as np
import pmt
from gnuradio import blocks, gr, gr_unittest

try:
    from gnuradio.hdradio import hd_decoder, nrsc5
    from gnuradio.hdradio.hd_decoder import MESSAGE_PORTS
except ImportError:  # running from the source tree
    from hdradio import hd_decoder, nrsc5
    from hdradio.hd_decoder import MESSAGE_PORTS


class FakeRadio:
    """Plays a script of (input sample index, event type, event) as samples
    are piped in, the way libnrsc5 fires events inside pipe calls."""

    def __init__(self, decoder, script):
        self.decoder = decoder
        self.script = sorted(script, key=lambda s: s[0])
        self.fed = 0
        self.errors = []

    def pipe_cf32(self, samples):
        self.fed += len(samples)
        while self.script and self.script[0][0] <= self.fed:
            _, t, e = self.script.pop(0)
            self.decoder._on_event(t, e)

    def close(self):
        pass


def n_outputs(block):
    return block.to_basic_block().output_signature().max_streams()


def audio(program, n, value):
    pcm = np.full((n, 2), value, dtype=np.float32)
    pcm[:, 1] = -value
    return (nrsc5.EventType.AUDIO, nrsc5.Audio(program, pcm.reshape(-1), 0))


def run(dec, script, n_in, stereo=True):
    """Run dec on n_in zero samples with the scripted events."""
    original_start = dec.start

    def start():
        ok = gr.basic_block.start(dec)
        dec._radio = FakeRadio(dec, script)
        return ok
    dec.start = start
    tb = gr.top_block()
    src = blocks.vector_source_c(np.zeros(n_in, dtype=np.complex64))
    tb.connect(src, dec)
    n_ports = len(dec.programs) * (2 if stereo else 1)
    sinks = [blocks.vector_sink_f() for _ in range(n_ports)]
    for i, s in enumerate(sinks):
        tb.connect((dec, i), s)
    debug = {p: blocks.message_debug() for p in MESSAGE_PORTS}
    for p, d in debug.items():
        tb.msg_connect(dec, p, d, "store")
    tb.run()
    dec.start = original_start
    msgs = {p: [d.get_message(i) for i in range(d.num_messages())]
            for p, d in debug.items()}
    return sinks, msgs


class qa_hd_decoder(gr_unittest.TestCase):

    def test_ports(self):
        self.assertEqual(n_outputs(hd_decoder("fm", [0, 1, 2], True)), 6)
        self.assertEqual(n_outputs(hd_decoder("fm", [3], False)), 1)
        with self.assertRaises(ValueError):
            hd_decoder("fm", [0, 0])
        with self.assertRaises(ValueError):
            hd_decoder("xm", [0])

    def test_paced_rate_preroll_and_dropout(self):
        # 135 inputs make 8 audio samples at FM. Feed 135 * 10000 inputs.
        dec = hd_decoder("fm", [0], True, paced=True, preroll=0.1)  # 4410
        script = [(1000, *audio(0, 2048, 0.5)),
                  (2000, *audio(0, 2048, 0.5)),
                  (3000, *audio(0, 2048, 0.5)),   # 6144 buffered: plays
                  (500000, *audio(0, 4096, 0.25))]
        sinks, _ = run(dec, script, 135 * 10000)
        left = np.array(sinks[0].data())
        right = np.array(sinks[1].data())
        self.assertEqual(len(left), 80000)          # exactly 8/135 of input
        np.testing.assert_array_equal(right, -left)
        played = np.flatnonzero(left == 0.5)
        self.assertEqual(len(played), 6144)         # all of it, in one run
        self.assertEqual(played[-1] - played[0], 6143)
        # The fourth event (4096) is below the 4410 preroll: it waits.
        self.assertEqual(np.count_nonzero(left == 0.25), 0)

    def test_burst_outputs_only_audio(self):
        dec = hd_decoder("fm", [0, 1], False, paced=False)
        script = [(10, *audio(0, 2048, 0.5)), (20, *audio(1, 1024, 0.5)),
                  (30, *audio(2, 4096, 0.5))]       # program 2 not selected
        sinks, _ = run(dec, script, 50000, stereo=False)
        self.assertEqual(len(sinks[0].data()), 2048)
        self.assertEqual(len(sinks[1].data()), 1024)
        # Mono is the mean of left and right, here 0.5 and -0.5.
        self.assertEqual(max(abs(x) for x in sinks[0].data()), 0.0)

    def test_id3_tag_on_new_song_only(self):
        dec = hd_decoder("fm", [0], True, paced=False)
        id3 = lambda title: (nrsc5.EventType.ID3, nrsc5.ID3(  # noqa: E731
            0, title, "Artist", None, None, None, None, [], None))
        script = [(10, *audio(0, 100, 0.1)), (10, *id3("A")),
                  (20, *audio(0, 100, 0.1)), (20, *id3("A")),
                  (30, *audio(0, 100, 0.1)), (30, *id3("B")),
                  (40, *audio(0, 100, 0.1))]
        sinks, msgs = run(dec, script, 100)
        tags = sinks[0].tags()
        self.assertEqual([t.offset for t in tags], [100, 300])
        self.assertEqual([pmt.to_python(t.value)["title"] for t in tags],
                         ["A", "B"])
        self.assertEqual(len(msgs["id3"]), 3)

    def test_metadata_only(self):
        dec = hd_decoder("fm", [], True)
        self.assertEqual(n_outputs(dec), 0)
        script = [(5, nrsc5.EventType.STATION_NAME, nrsc5.StationName("WXYZ")),
                  (6, nrsc5.EventType.MER, nrsc5.MER(12.5, 13.0))]
        _, msgs = run(dec, script, 100)
        name = pmt.to_python(msgs["station"][0])
        self.assertEqual(name, {"event": "station_name", "name": "WXYZ"})
        self.assertEqual(pmt.to_python(msgs["status"][0])["event"], "mer")

    def test_lot_becomes_pdu(self):
        dec = hd_decoder("fm", [], True)
        service = nrsc5.SIGService(nrsc5.ServiceType.DATA, 3, "Art", [], None)
        comp = nrsc5.SIGComponent(nrsc5.ComponentType.DATA, 1, None, None)
        expiry = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)
        lot = nrsc5.LOT(0x1000, 7, nrsc5.MIMEType.PRIMARY_IMAGE, "cover.jpg",
                        b"\xff\xd8\xff\xe0data", expiry, service, comp)
        packet = nrsc5.PACKET(0x2000, 1, nrsc5.MIMEType.TEXT, b"hi", service,
                              comp)
        script = [(5, nrsc5.EventType.LOT, lot),
                  (6, nrsc5.EventType.PACKET, packet)]
        _, msgs = run(dec, script, 100)
        meta = pmt.to_python(pmt.car(msgs["files"][0]))
        data = bytes(pmt.u8vector_elements(pmt.cdr(msgs["files"][0])))
        self.assertEqual(data, b"\xff\xd8\xff\xe0data")
        self.assertEqual(meta["event"], "lot")
        self.assertEqual(meta["name"], "cover.jpg")
        self.assertEqual(meta["mime"], "primary_image")
        self.assertEqual(meta["port"], 0x1000)
        self.assertEqual(meta["service"]["name"], "Art")
        self.assertEqual(meta["expiry_utc"], "2027-01-01T00:00:00+00:00")
        pkt = pmt.to_python(pmt.car(msgs["data"][0]))
        self.assertEqual((pkt["event"], pkt["mime"]), ("packet", "text"))


if __name__ == "__main__":
    gr_unittest.run(qa_hd_decoder)
