# gr-hdradio

HD Radio (NRSC-5) reception in GNU Radio 3.10, built on
[nrsc5](https://github.com/theori-io/nrsc5). It's written for USAFA's ECE 448,
Software Defined Radios. Complex samples go in at any rate. Out come audio
streams for the programs you pick, plus station, song, service, and status
messages. Files the station sends, such as album art and logos, come out as
PDUs (GNU Radio's tagged binary messages).

It's Python only: the decoding is libnrsc5's, called through its own Python
binding. No compiler is needed for gr-hdradio itself, but nrsc5 has to be built
once (see [Install](#install)).

## Blocks

All three are under **[HD Radio]** in GRC.

| Block | In | Out |
| :-- | :-- | :-- |
| **HD Radio Receiver** | complex at any sample rate, with an optional station offset | the decoder's outputs |
| **HD Radio Decoder** | complex at nrsc5's native rate: 744,187.5 S/s FM, 46,511.71875 S/s AM | audio streams and message ports |
| **HD File Sink** | the `files` port | files in a folder; a `saved` message for each one |

**HD Radio Receiver** shifts the station to 0 Hz by **Station Offset**, then
resamples to the native rate:

- 1,488,375 S/s (a PlutoSDR set to that rate) or 2,976,750 S/s: a Rational
  Resampler, decimating by exactly 2 or 4.
- 744,187.5 S/s: no resampler.
- Any other rate, such as 2 MS/s: a polyphase arbitrary resampler.

### Parameters

| Parameter | Meaning |
| :-- | :-- |
| Programs | which programs to decode, in output order: `[0]` is HD1, `[0, 1]` is HD1 and HD2, and `[]` decodes metadata only |
| Channels | Stereo: a left and a right port per program. Mono: one port per program |
| Audio Timing | **Paced**: every port runs at exactly 44,100 samples per second of input, silent until **Preroll** seconds of audio are buffered and after a dropout. Use it for an Audio Sink. **Burst**: only decoded audio, as soon as it's decoded. Use it for WAV or file sinks |
| Mode | FM or AM |

An `id3` stream tag on the audio marks the first sample of each new song.

### Message ports

Metadata goes out as PMT dicts; their `event` key names the event. Binary
data goes out as PDUs, a `(metadata . u8vector)` pair whose metadata has an
`event` key too.

| Port | Events |
| :-- | :-- |
| `status` | `sync`, `lost_sync`, `mer`, `ber`, `agc` |
| `station` | `station_name`, `station_slogan`, `station_message`, `station_location`, `station_id`, `emergency_alert`, `audio_service_descriptor`, `data_service_descriptor`, `local_time`, `leap_second_offset`, `exciter_info`, `importer_info` |
| `services` | `sig` (the service list), `audio_service` |
| `id3` | `id3`: title, artist, album, genre, and more, with the program number |
| `files` | PDUs: `lot` (album art, station logos, other LOT files, meaning files sent by Large Object Transfer), `here_image` (traffic and weather maps) |
| `data` | PDUs: `packet`, `stream` (data services) |
| `hdc` | PDUs: `hdc`, the raw audio packets of every program |

Message Debug prints any of them, and GNU Radio's PDU blocks take the PDU
ports.

### HD File Sink

It writes each `files` PDU to **Directory**. **Data Ports** and **MIME Types**
narrow which files it keeps. For example, `['primary_image', 'station_logo']`
keeps only album art and logos. Stations resend every file every few seconds,
so by default a name already written is skipped.

File names come from the station, so the sink keeps only the last part of a
name and turns odd characters into `_`. A file can never land outside
Directory. A name with no extension gets one from the file's first bytes.

## Examples

- `examples/hd_rx_file.grc` decodes HD1 and HD2 from a cu8 recording at
  1,488,375 S/s to `hd1.wav` and `hd2.wav`. It also saves station files to
  `hd_files/` and prints station and song information. To try it on nrsc5's
  sample capture (KUT 90.5, Austin):

  ```sh
  git clone https://github.com/theori-io/nrsc5 && xz -d < nrsc5/support/sample.xz > sample
  grcc examples/hd_rx_file.grc -o . && python3 hd_rx_file.py -f sample
  ```

- `examples/hd_rx_pluto.grc` plays a station live from a PlutoSDR. It decodes
  all four programs; the HD1 to HD4 buttons switch which one plays. Receive
  only.

## Install

nrsc5 is not on conda-forge, so radioconda doesn't have it. Build it first,
then install gr-hdradio into the same place.

### Linux or macOS, radioconda

```sh
conda activate radioconda       # or your GNU Radio environment
conda install -c conda-forge c-compiler cmake make patch pkg-config \
    autoconf automake libtool fftw
scripts/install_nrsc5.sh        # libnrsc5 and nrsc5 into $CONDA_PREFIX
cmake -S . -B build -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX
cmake --install build
```

### Ubuntu or Debian, GNU Radio from apt

```sh
sudo apt install gnuradio cmake build-essential git autoconf automake \
    libtool patch pkg-config libfftw3-dev libao-dev librtlsdr-dev
scripts/install_nrsc5.sh --prefix /usr
cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/usr \
    -DPYTHON_EXECUTABLE=/usr/bin/python3 \
    -DGR_PYTHON_DIR=/usr/lib/python3/dist-packages
sudo cmake --install build
```

### Windows, radioconda

1. Get `libnrsc5.dll` and `nrsc5.exe`. Either:
   - download the `nrsc5-win64` artifact from this repo's latest CI run, or
   - build them on Linux with `scripts/install_nrsc5.sh --windows nrsc5-win64`
     (needs `mingw-w64`).
2. Install gr-hdradio from the radioconda prompt:
   ```bat
   conda install -c conda-forge cmake
   cmake -S . -B build -DCMAKE_INSTALL_PREFIX=%CONDA_PREFIX%\Library
   cmake --install build
   ```
3. Copy `libnrsc5.dll` into the installed `Lib\site-packages\gnuradio\hdradio`
   folder, or set `HDRADIO_NRSC5_LIB` to the DLL's full path.
4. Put `nrsc5.exe` on your PATH for command-line decoding.

### Which nrsc5

`scripts/install_nrsc5.sh` builds nrsc5 commit `631abc1` from its master
branch. Only master takes complex float input (`nrsc5_pipe_samples_cf32` and
`--iq-input-format cf32`). v3.2.0, the latest release, takes only cu8 and
cs16. `python/hdradio/nrsc5.py` is nrsc5's Python binding from the same
commit, so move the two together.

gr-hdradio looks for libnrsc5 in this order:

1. `$HDRADIO_NRSC5_LIB`
2. the package's own folder
3. the running Python's prefix
4. the system loader

## Tests

```sh
cmake -S . -B build && ctest --test-dir build --output-on-failure
```

The decoder and file-sink tests feed the blocks scripted nrsc5 events. Set
`HDRADIO_SAMPLE` to an unpacked nrsc5 `sample` to also decode a real capture
end to end. On that capture, Burst audio matches the `nrsc5` program's own
output sample for sample at 744,187.5, 1,488,375, 2,976,750, and 2,000,000
S/s, including 2 MS/s with the station 250 kHz off center.

## License

GPL-3.0-or-later, the same as GNU Radio and nrsc5. `python/hdradio/nrsc5.py`
is from nrsc5 (copyright its authors) and is unchanged except for a header
saying where it came from.
