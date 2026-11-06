#
# Copyright 2026 Neil Rogers.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""HD Radio (NRSC-5) decoder: complex samples at nrsc5's native rate in,
audio streams and metadata messages out."""

import collections

import numpy as np
import pmt
from gnuradio import gr

from . import nrsc5
from .radio import (AUDIO_PER_INPUT, AUDIO_RATE, NATIVE_RATE, Radio,
                    message, pdu, to_plain)

# Message ports, in the order GRC shows them.
MESSAGE_PORTS = ("status", "station", "services", "id3", "files", "data",
                 "hdc")

_STATUS = {
    nrsc5.EventType.SYNC: "sync",
    nrsc5.EventType.LOST_SYNC: "lost_sync",
    nrsc5.EventType.MER: "mer",
    nrsc5.EventType.BER: "ber",
    nrsc5.EventType.AGC: "agc",
}
_STATION = {
    nrsc5.EventType.STATION_ID: "station_id",
    nrsc5.EventType.STATION_NAME: "station_name",
    nrsc5.EventType.STATION_SLOGAN: "station_slogan",
    nrsc5.EventType.STATION_MESSAGE: "station_message",
    nrsc5.EventType.STATION_LOCATION: "station_location",
    nrsc5.EventType.AUDIO_SERVICE_DESCRIPTOR: "audio_service_descriptor",
    nrsc5.EventType.DATA_SERVICE_DESCRIPTOR: "data_service_descriptor",
    nrsc5.EventType.EMERGENCY_ALERT: "emergency_alert",
    nrsc5.EventType.EXCITER_INFO: "exciter_info",
    nrsc5.EventType.IMPORTER_INFO: "importer_info",
    nrsc5.EventType.LEAP_SECOND_OFFSET: "leap_second_offset",
    nrsc5.EventType.LOCAL_TIME: "local_time",
}


def _service(svc):
    """The short form of an SIG service: enough to name it in a message."""
    if svc is None:
        return None
    return {"number": svc.number, "name": svc.name,
            "type": to_plain(svc.type)}


def _component(comp):
    if comp is None:
        return None
    return {"id": comp.id, "type": to_plain(comp.type)}


class _Program:
    """Decoded audio for one program, waiting to go out its ports."""

    def __init__(self):
        self.chunks = collections.deque()   # float32 arrays, shape (n, 2)
        self.buffered = 0                   # samples in chunks
        self.head = 0                       # samples already taken from chunks[0]
        self.written = 0                    # samples ever added
        self.read = 0                       # samples ever taken
        self.playing = False                # paced mode: past the preroll
        self.tags = collections.deque()     # (sample index, key, value)
        self.last_id3 = None

    def add(self, pcm):
        self.chunks.append(pcm)
        self.buffered += len(pcm)
        self.written += len(pcm)

    def take(self, n):
        """Up to n samples, shape (k, 2), oldest first."""
        out = np.empty((min(n, self.buffered), 2), dtype=np.float32)
        filled = 0
        while filled < len(out):
            chunk = self.chunks[0]
            k = min(len(out) - filled, len(chunk) - self.head)
            out[filled:filled + k] = chunk[self.head:self.head + k]
            filled += k
            self.head += k
            if self.head == len(chunk):
                self.chunks.popleft()
                self.head = 0
        self.buffered -= filled
        self.read += filled
        return out

    def drop(self, n):
        self.take(n)


class hd_decoder(gr.basic_block):
    """HD Radio (NRSC-5) decoder built on libnrsc5.

    Input: complex samples at nrsc5's native rate, 744,187.5 S/s for FM or
    46,511.71875 S/s for AM, centered on the station. HD Radio Receiver wraps
    this block with a resampler for any other rate.

    Outputs: for each program listed in `programs`, in that order, a left and
    a right float stream at 44,100 S/s (one mixed-down stream if `stereo` is
    False). An "id3" stream tag marks the first sample of each new song.

    `paced` True: each output advances at exactly 44,100 samples for every
    744,187.5 input samples, with silence until a program has `preroll`
    seconds of audio buffered and after any dropout. Use it to feed an Audio
    Sink. `paced` False: audio comes out as soon as nrsc5 decodes it and
    nothing else does; use it for file sinks and offline work.

    Message ports (PMT dicts whose "event" key names the event, or PDUs
    whose metadata dict has one):
      status    sync, lost_sync, mer, ber, agc
      station   station name, slogan, message, location, ID, alerts, time
      services  SIG service list and audio service details
      id3       song title, artist, album, genre, and so on, per program
      files     PDUs: complete LOT files (album art, logos) and HERE images
      data      PDUs: packet and stream data services
      hdc       PDUs: raw HDC audio packets, every program
    """

    def __init__(self, mode="fm", programs=(0,), stereo=True, paced=True,
                 preroll=0.5):
        mode = str(mode).lower()
        if mode not in NATIVE_RATE:
            raise ValueError("mode must be 'fm' or 'am'")
        programs = [int(p) for p in programs]
        if len(set(programs)) != len(programs):
            raise ValueError("programs lists a program twice")
        if any(p < 0 or p > 7 for p in programs):
            raise ValueError("programs must be between 0 and 7")

        self.mode = mode
        self.programs = programs
        self.stereo = bool(stereo)
        self.paced = bool(paced)
        self.preroll = max(0, int(round(float(preroll) * AUDIO_RATE)))
        # Paced mode keeps at most this much audio; more means the station's
        # clock is running fast against ours, so the oldest audio is dropped.
        self.max_buffer = max(4 * self.preroll, AUDIO_RATE)
        self.ports_per_program = 2 if self.stereo else 1

        n_out = len(programs) * self.ports_per_program
        gr.basic_block.__init__(
            self, name="hd_decoder", in_sig=[np.complex64],
            out_sig=[np.float32] * n_out if n_out else None)
        for port in MESSAGE_PORTS:
            self.message_port_register_out(pmt.intern(port))

        self._num, self._den = AUDIO_PER_INPUT[mode]
        self._in_total = 0
        self._out_total = 0
        self._state = {p: _Program() for p in programs}
        self._radio = None
        self._queue = []        # (port, message) published after each work call

    # --- lifecycle -------------------------------------------------------

    def start(self):
        self._radio = Radio(self._on_event)
        self._radio.open_pipe()
        self._radio.set_mode(nrsc5.Mode.AM if self.mode == "am"
                             else nrsc5.Mode.FM)
        return super().start()

    def stop(self):
        if self._radio is not None:
            self._radio.close()
            self._radio = None
        return super().stop()

    @property
    def native_rate(self):
        return NATIVE_RATE[self.mode]

    # --- nrsc5 events (called inside pipe_cf32, on this block's thread) ---

    def _publish(self, port, msg):
        self._queue.append((port, msg))

    def _on_event(self, evt_type, evt):
        try:
            self._handle(evt_type, evt)
        except Exception as err:  # never let an exception unwind into C
            gr.log.error(f"hd_decoder: {evt_type.name}: {err}")

    def _handle(self, t, e):
        E = nrsc5.EventType
        if t == E.AUDIO:
            st = self._state.get(e.program)
            if st is not None:
                st.add(e.data.reshape(-1, 2))
        elif t in _STATUS:
            self._publish("status", message(_STATUS[t], e))
        elif t in _STATION:
            self._publish("station", message(_STATION[t], e))
        elif t == E.ID3:
            fields = to_plain(e)
            self._publish("id3", message("id3", fields))
            st = self._state.get(e.program)
            if st is not None:
                tag = {k: fields.get(k) for k in
                       ("title", "artist", "album", "genre")}
                if tag != st.last_id3:      # stations repeat ID3 often
                    st.last_id3 = tag
                    st.tags.append((st.written, "id3", tag))
        elif t == E.SIG:
            services = [{"number": s.number, "name": s.name,
                         "type": to_plain(s.type),
                         "components": to_plain(s.components)}
                        for s in e.services]
            self._publish("services", message("sig", {"services": services}))
        elif t == E.AUDIO_SERVICE:
            self._publish("services", message("audio_service", e))
        elif t == E.LOT:
            meta = {"event": "lot", "port": e.port, "lot": e.lot,
                    "mime": to_plain(e.mime), "name": e.name or "",
                    "size": len(e.data), "expiry_utc": to_plain(e.expiry_utc),
                    "service": _service(e.service),
                    "component": _component(e.component)}
            self._publish("files", pdu(meta, e.data))
        elif t == E.HERE_IMAGE:
            fields = to_plain(e)
            data = fields.pop("data")
            fields["event"] = "here_image"
            fields["size"] = len(data)
            self._publish("files", pdu(fields, data))
        elif t in (E.STREAM, E.PACKET):
            meta = {"event": t.name.lower(), "port": e.port, "seq": e.seq,
                    "mime": to_plain(e.mime), "service": _service(e.service),
                    "component": _component(e.component)}
            self._publish("data", pdu(meta, e.data))
        elif t == E.HDC:
            meta = {"event": "hdc", "program": e.program,
                    "flags": int(e.flags)}
            self._publish("hdc", pdu(meta, e.data))

    # --- streaming -------------------------------------------------------

    def forecast(self, noutput_items, ninputs):
        if not self.programs or not self.paced:
            return [1] * ninputs
        # A hint, not a promise: ask for about what noutput_items needs, but
        # let a single input through so the last few samples of a finite
        # stream are not stranded below the threshold.
        need = (max(noutput_items - 1, 0) * self._den) // self._num + 1
        return [min(need, 4096)] * ninputs

    def _feed(self, samples):
        if len(samples):
            self._radio.pipe_cf32(samples)
            for err in self._radio.errors:
                gr.log.warn(f"hd_decoder: skipped an nrsc5 {err}")
            self._radio.errors.clear()
        self._in_total += len(samples)
        self.consume(0, len(samples))

    def _flush_messages(self):
        queue, self._queue = self._queue, []
        for port, msg in queue:
            self.message_port_pub(pmt.intern(port), msg)

    def _write(self, output_items, index, out_offset, pcm):
        """Write (n, 2) audio for program `index` at out_offset."""
        base = index * self.ports_per_program
        n = len(pcm)
        if self.stereo:
            output_items[base][out_offset:out_offset + n] = pcm[:, 0]
            output_items[base + 1][out_offset:out_offset + n] = pcm[:, 1]
        else:
            output_items[base][out_offset:out_offset + n] = pcm.mean(axis=1)

    def _zero(self, output_items, index, out_offset, n):
        base = index * self.ports_per_program
        for port in range(base, base + self.ports_per_program):
            output_items[port][out_offset:out_offset + n] = 0

    def _tag(self, index, st, first_read, out_offset, n):
        """Attach tags whose audio sample left in [first_read, first_read + n)."""
        base = index * self.ports_per_program
        while st.tags and st.tags[0][0] < first_read + n:
            sample, key, value = st.tags.popleft()
            pos = max(sample, first_read) - first_read + out_offset
            for port in range(base, base + self.ports_per_program):
                self.add_item_tag(port, self.nitems_written(port) + pos,
                                  pmt.intern(key), message(key, value),
                                  pmt.intern(self.alias()))

    def general_work(self, input_items, output_items):
        samples = input_items[0]

        if not self.programs:
            self._feed(samples)
            self._flush_messages()
            return 0

        if self.paced:
            return self._work_paced(samples, output_items)
        return self._work_burst(samples, output_items)

    def _work_paced(self, samples, output_items):
        space = len(output_items[0])
        # Take as many inputs as the output space allows, exactly at the
        # 8/135 (FM) or 128/135 (AM) audio-to-input ratio.
        limit = ((self._out_total + space + 1) * self._den - 1) // self._num
        k = max(0, min(len(samples), limit - self._in_total))
        self._feed(samples[:k])
        n = self._in_total * self._num // self._den - self._out_total

        for index, program in enumerate(self.programs):
            st = self._state[program]
            if st.buffered > self.max_buffer:
                st.drop(st.buffered - self.preroll)
            if not st.playing and st.buffered >= max(self.preroll, 1):
                st.playing = True
            done = 0
            if st.playing:
                first = st.read
                pcm = st.take(n)
                self._write(output_items, index, 0, pcm)
                self._tag(index, st, first, 0, len(pcm))
                done = len(pcm)
                if done < n:            # dropout: go quiet and buffer again
                    st.playing = False
            if done < n:
                self._zero(output_items, index, done, n - done)

        self._out_total += n
        self._flush_messages()
        return n

    def _work_burst(self, samples, output_items):
        self._feed(samples)
        space = len(output_items[0])
        for index, program in enumerate(self.programs):
            st = self._state[program]
            first = st.read
            pcm = st.take(space)
            self._write(output_items, index, 0, pcm)
            self._tag(index, st, first, 0, len(pcm))
            base = index * self.ports_per_program
            for port in range(base, base + self.ports_per_program):
                self.produce(port, len(pcm))
        self._flush_messages()
        return gr.WORK_CALLED_PRODUCE
