#
# Copyright 2026 Neil Rogers.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""HD File Sink: write the files an HD Radio station sends to a folder."""

import os
import re
import tempfile

import pmt
from gnuradio import gr

# Extension for a file whose name carries none, by nrsc5's MIME type name.
_EXT = {"jpeg": ".jpg",
        "png": ".png", "text": ".txt", "ttn_tpeg_1": ".tpeg",
        "ttn_tpeg_2": ".tpeg", "ttn_tpeg_3": ".tpeg", "ttn_stm_traffic": ".bin",
        "ttn_stm_weather": ".bin", "here_tpeg": ".tpeg", "here_image": ".png",
        "hd_tmc": ".bin", "hdc": ".hdc"}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name, fallback):
    """A file name from the air, made safe to write: no folders, no leading
    dots, no odd characters. The station chooses LOT names, so never trust
    them as paths."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = _SAFE.sub("_", name).lstrip(".")[:128]
    return name or fallback


def _sniff(data):
    """An extension from the file's first bytes: album art and logos are
    JPEG or PNG whatever their MIME type says."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def _meta(msg):
    meta = pmt.car(msg)
    return pmt.to_python(meta) if pmt.is_dict(meta) else {}


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class hd_file_sink(gr.basic_block):
    """Write each file from HD Radio Decoder's "files" port to `directory`.

    `ports`: the data ports (LOT service ports) to keep; empty keeps all.
    `mime_types`: nrsc5 MIME type names to keep, for example
    ["primary_image", "station_logo"]; empty keeps all.
    `overwrite`: stations repeat their files every few seconds, so by default
    a name already written is skipped.

    Each file written is announced on the "saved" port as a dict with the
    path and the file's metadata.
    """

    def __init__(self, directory="hd_files", ports=(), mime_types=(),
                 overwrite=False):
        gr.basic_block.__init__(self, name="hd_file_sink", in_sig=None,
                                out_sig=None)
        self.directory = str(directory)
        self.ports = {int(p) for p in _as_list(ports)}
        self.mime_types = {str(m).lower() for m in _as_list(mime_types)}
        self.overwrite = bool(overwrite)
        self.saved = 0
        self.message_port_register_in(pmt.intern("files"))
        self.set_msg_handler(pmt.intern("files"), self._handle)
        self.message_port_register_out(pmt.intern("saved"))

    def wanted(self, meta):
        if self.ports and meta.get("port") not in self.ports:
            return False
        if self.mime_types and str(meta.get("mime", "")).lower() not in self.mime_types:
            return False
        return True

    def path_for(self, meta, data=b""):
        kind = meta.get("event", "file")
        if kind == "here_image":
            fallback = f"here_{meta.get('image_type', 'image')}_{meta.get('seq', 0)}"
        else:
            fallback = f"lot_{meta.get('port', 0)}_{meta.get('lot', 0)}"
        name = safe_name(meta.get("name"), fallback)
        if "." not in name:
            name += _sniff(data) or _EXT.get(str(meta.get("mime", kind)).lower(), ".bin")
        return os.path.join(self.directory, name)

    def _handle(self, msg):
        if not pmt.is_pair(msg) or not pmt.is_u8vector(pmt.cdr(msg)):
            gr.log.warn("hd_file_sink: expected a PDU")
            return
        meta = _meta(msg)
        if not self.wanted(meta):
            return
        data = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
        path = self.path_for(meta, data)
        if os.path.exists(path) and not self.overwrite:
            return
        os.makedirs(self.directory, exist_ok=True)
        # Write beside the target, then rename, so a reader never sees half.
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".hd_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        self.saved += 1
        note = pmt.make_dict()
        note = pmt.dict_add(note, pmt.intern("path"), pmt.intern(path))
        note = pmt.dict_add(note, pmt.intern("meta"), pmt.car(msg))
        self.message_port_pub(pmt.intern("saved"), note)
