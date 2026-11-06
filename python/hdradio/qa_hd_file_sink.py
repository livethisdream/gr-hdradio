#
# Copyright 2026 Neil Rogers.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""HD File Sink: names from the air are made safe, filters, repeats."""

import os
import tempfile

import pmt
from gnuradio import gr_unittest

try:
    from gnuradio.hdradio import hd_file_sink, safe_name
except ImportError:  # running from the source tree
    from hdradio import hd_file_sink, safe_name

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 20


def lot(name, data, port=0x1000, mime="primary_image", lot_id=1):
    meta = pmt.make_dict()
    for k, v in (("event", pmt.intern("lot")), ("name", pmt.intern(name)),
                 ("port", pmt.from_long(port)), ("lot", pmt.from_long(lot_id)),
                 ("mime", pmt.intern(mime))):
        meta = pmt.dict_add(meta, pmt.intern(k), v)
    return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))


class qa_hd_file_sink(gr_unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def test_safe_name(self):
        self.assertEqual(safe_name("../../etc/passwd", "x"), "passwd")
        self.assertEqual(safe_name("..\\..\\boot.ini", "x"), "boot.ini")
        self.assertEqual(safe_name(".hidden", "x"), "hidden")
        self.assertEqual(safe_name("a b?c.jpg", "x"), "a_b_c.jpg")
        self.assertEqual(safe_name("", "lot_1"), "lot_1")
        self.assertEqual(safe_name("..", "lot_1"), "lot_1")

    def test_writes_inside_directory_only(self):
        sink = hd_file_sink(self.dir)
        sink._handle(lot("../../escape.jpg", JPEG))
        self.assertEqual(os.listdir(self.dir), ["escape.jpg"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name,
                                                     "escape.jpg")))

    def test_extension_from_content(self):
        sink = hd_file_sink(self.dir)
        sink._handle(lot("", PNG, lot_id=9))
        sink._handle(lot("cover", JPEG, lot_id=10))
        self.assertEqual(sorted(os.listdir(self.dir)),
                         ["cover.jpg", "lot_4096_9.png"])

    def test_filters(self):
        sink = hd_file_sink(self.dir, ports=[0x1000],
                            mime_types=["station_logo"])
        sink._handle(lot("a.png", PNG, port=0x1000, mime="station_logo"))
        sink._handle(lot("b.png", PNG, port=0x2000, mime="station_logo"))
        sink._handle(lot("c.jpg", JPEG, port=0x1000, mime="primary_image"))
        self.assertEqual(os.listdir(self.dir), ["a.png"])

    def test_repeats_skipped_unless_overwrite(self):
        sink = hd_file_sink(self.dir)
        sink._handle(lot("a.jpg", JPEG))
        sink._handle(lot("a.jpg", JPEG + b"new"))
        with open(os.path.join(self.dir, "a.jpg"), "rb") as f:
            self.assertEqual(f.read(), JPEG)
        self.assertEqual(sink.saved, 1)
        sink = hd_file_sink(self.dir, overwrite=True)
        sink._handle(lot("a.jpg", JPEG + b"new"))
        with open(os.path.join(self.dir, "a.jpg"), "rb") as f:
            self.assertEqual(f.read(), JPEG + b"new")

    def test_ignores_non_pdu(self):
        sink = hd_file_sink(self.dir)
        sink._handle(pmt.intern("not a pdu"))
        self.assertFalse(os.path.exists(self.dir))


if __name__ == "__main__":
    gr_unittest.run(qa_hd_file_sink)
