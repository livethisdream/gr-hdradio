#
# Copyright 2026 Neil Rogers.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Glue between libnrsc5 and GNU Radio: loading the library, a faster audio
path than the stock binding, and turning nrsc5 events into PMTs."""

import ctypes
import ctypes.util
import datetime
import enum
import os
import platform
import sys

import numpy as np
import pmt

from . import nrsc5

# Sample rates nrsc5 expects for complex float input, and the audio rate it
# produces. FM is 1488375 / 2 and AM is 1488375 / 32, exactly.
NATIVE_RATE = {"fm": 744187.5, "am": 46511.71875}
AUDIO_RATE = 44100

# Audio samples out per complex sample in, as an exact fraction (num, den):
# 44100 / 744187.5 = 8 / 135 and 44100 / 46511.71875 = 128 / 135.
AUDIO_PER_INPUT = {"fm": (8, 135), "am": (128, 135)}


def _library_names():
    system = platform.system()
    if system == "Windows":
        return ["libnrsc5.dll", "nrsc5.dll"]
    if system == "Darwin":
        return ["libnrsc5.dylib"]
    return ["libnrsc5.so", "libnrsc5.so.0"]


def load_library():
    """Find and load libnrsc5, once.

    Search order: $HDRADIO_NRSC5_LIB (a full path), this package's folder
    (where the Windows build puts the DLL), the running Python's prefix
    (radioconda), then the system loader.
    """
    if nrsc5.NRSC5.libnrsc5 is not None:
        return nrsc5.NRSC5.libnrsc5

    candidates = []
    env = os.environ.get("HDRADIO_NRSC5_LIB")
    if env:
        candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    prefixes = [here, os.path.join(sys.prefix, "lib"),
                os.path.join(sys.prefix, "Library", "bin")]
    for prefix in prefixes:
        for name in _library_names():
            candidates.append(os.path.join(prefix, name))
    found = ctypes.util.find_library("nrsc5")
    if found:
        candidates.append(found)
    candidates.extend(_library_names())

    errors = []
    for path in candidates:
        if os.path.isabs(path) and not os.path.exists(path):
            continue
        try:
            if platform.system() == "Windows" and os.path.isabs(path):
                with os.add_dll_directory(os.path.dirname(path)):
                    lib = ctypes.cdll.LoadLibrary(path)
            else:
                lib = ctypes.cdll.LoadLibrary(path)
        except OSError as err:
            errors.append(f"{path}: {err}")
            continue
        nrsc5.NRSC5.libnrsc5 = lib
        return lib

    raise nrsc5.NRSC5Error(
        "libnrsc5 not found. Build it with scripts/install_nrsc5.sh from "
        "gr-hdradio, or set HDRADIO_NRSC5_LIB to the library's full path.\n"
        + "\n".join(errors))


def has_cf32(lib=None):
    """True if this libnrsc5 accepts complex float input (nrsc5 after v3.2.0)."""
    lib = lib or load_library()
    return hasattr(lib, "nrsc5_pipe_samples_cf32")


class Radio(nrsc5.NRSC5):
    """nrsc5.NRSC5 in pipe mode, with audio delivered as numpy arrays.

    The stock binding turns every audio buffer into a Python list of ints;
    this reads it straight out of the C buffer instead, and drops IQ events,
    which pipe mode does not need.
    """

    def __init__(self, callback):
        load_library()
        super().__init__(callback)
        self.errors = []
        lib = nrsc5.NRSC5.libnrsc5
        lib.nrsc5_pipe_samples_cf32.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_uint]
        lib.nrsc5_pipe_samples_cf32.restype = ctypes.c_int

    def _callback_wrapper(self, c_evt):
        evt = c_evt.contents
        if evt.event == nrsc5.EventType.IQ.value:
            return
        if evt.event == nrsc5.EventType.AUDIO.value:
            audio = evt.u.audio
            # count is the number of int16 values, left and right interleaved
            ptr = ctypes.cast(audio.data, ctypes.POINTER(ctypes.c_int16))
            data = np.ctypeslib.as_array(ptr, shape=(audio.count,))
            pcm = data.astype(np.float32) * (1.0 / 32768.0)
            self.callback(nrsc5.EventType.AUDIO,
                          nrsc5.Audio(audio.program, pcm, audio.flags))
            return
        try:
            super()._callback_wrapper(c_evt)
        except Exception as err:
            # An unknown enum value (a MIME type nrsc5's binding has not
            # named, say) must not unwind into C; skip that one event.
            self.errors.append(f"event {evt.event}: {err!r}")

    def pipe_cf32(self, samples):
        """Push complex64 samples at the native rate. Events fire before this
        returns, on the calling thread."""
        samples = np.ascontiguousarray(samples, dtype=np.complex64)
        ptr = samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        result = nrsc5.NRSC5.libnrsc5.nrsc5_pipe_samples_cf32(
            self.radio, ptr, 2 * len(samples))
        if result != 0:
            raise nrsc5.NRSC5Error("Failed to pipe samples.")


# --- nrsc5 events to plain Python, then to PMT -----------------------------

def to_plain(obj):
    """Turn an nrsc5 event (namedtuples, enums, datetimes) into dicts, lists,
    strings, and numbers that pmt.to_pmt can take."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, enum.Enum):
        return obj.name.lower()
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if hasattr(obj, "_asdict"):
        return {k: to_plain(v) for k, v in obj._asdict().items()}
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return str(obj)


def to_pmt(value):
    """pmt.to_pmt, but None becomes PMT_NIL, bytes a u8vector, and lists
    vectors, so Message Debug prints them readably."""
    if value is None:
        return pmt.PMT_NIL
    if isinstance(value, bool):
        return pmt.from_bool(value)
    if isinstance(value, int):
        return pmt.from_long(value)
    if isinstance(value, float):
        return pmt.from_double(value)
    if isinstance(value, str):
        return pmt.intern(value)
    if isinstance(value, bytes):
        return pmt.init_u8vector(len(value), list(value))
    if isinstance(value, dict):
        d = pmt.make_dict()
        for k, v in value.items():
            d = pmt.dict_add(d, pmt.intern(k), to_pmt(v))
        return d
    if isinstance(value, (list, tuple)):
        vec = pmt.make_vector(len(value), pmt.PMT_NIL)
        for i, v in enumerate(value):
            pmt.vector_set(vec, i, to_pmt(v))
        return vec
    return pmt.intern(str(value))


def message(kind, fields):
    """A metadata message: a PMT dict whose "event" key names the event."""
    plain = dict(to_plain(fields)) if fields is not None else {}
    plain["event"] = kind
    return to_pmt(plain)


def pdu(meta, data):
    """A PDU: (meta dict . u8vector), GNU Radio's convention for binary data."""
    data = bytes(data)
    vec = pmt.init_u8vector(len(data), np.frombuffer(data, dtype=np.uint8)
                            if data else [])
    return pmt.cons(to_pmt(meta), vec)
