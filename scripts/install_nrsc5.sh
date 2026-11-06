#!/usr/bin/env bash
# Build nrsc5 (library and command-line program) from a pinned commit and
# install it where GNU Radio will find it.
#
#   scripts/install_nrsc5.sh                  # into $CONDA_PREFIX, else /usr/local
#   scripts/install_nrsc5.sh --prefix /usr    # somewhere else
#   scripts/install_nrsc5.sh --windows DIR    # cross-compile a Windows build
#                                             # (needs mingw-w64) into DIR
#
# Why a build: nrsc5 is not on conda-forge, so radioconda does not ship it.
# Why this commit: complex float input (nrsc5_pipe_samples_cf32, and the
# command line's --iq-input-format cf32) is on nrsc5's master branch but in no
# release yet; v3.2.0 takes only cu8 and cs16. Move NRSC5_REV and the
# vendored python/hdradio/nrsc5.py together.
#
# Needs: a C compiler, cmake, git, make, patch, pkg-config, autoconf,
# automake, libtool, and network access to github.com (nrsc5's build fetches
# and patches its own FAAD2). FFTW, libao, and rtl-sdr are used if installed
# and built from source if not. In radioconda on Linux or macOS:
#
#   conda install -c conda-forge c-compiler cmake make patch pkg-config \
#       autoconf automake libtool fftw
set -euo pipefail

NRSC5_URL=https://github.com/theori-io/nrsc5.git
NRSC5_REV=631abc16f0c09537c3acedba65dd18a7d0c15866   # master, 2026-10-17

PREFIX="${CONDA_PREFIX:-/usr/local}"
WINDOWS=""
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --windows) WINDOWS="$2"; shift 2 ;;
    -j*) JOBS="${1#-j}"; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

log() { printf '[install_nrsc5] %s\n' "$*"; }
SRC="$(mktemp -d)"
trap 'rm -rf "$SRC"' EXIT

log "fetching nrsc5 ${NRSC5_REV:0:7}"
git clone -q "$NRSC5_URL" "$SRC/nrsc5"
git -C "$SRC/nrsc5" checkout -q "$NRSC5_REV"

if [ -n "$WINDOWS" ]; then
  # nrsc5's own cross-compile recipe: everything static, one DLL and one EXE.
  log "cross-compiling for 64-bit Windows"
  (cd "$SRC/nrsc5" && bash support/win-cross-compile 64 -j"$JOBS" >/dev/null)
  mkdir -p "$WINDOWS"
  cp "$SRC/nrsc5/build-win64/bin/"*.dll "$SRC/nrsc5/build-win64/bin/nrsc5.exe" "$WINDOWS/"
  log "Windows build in $WINDOWS:"
  ls -1 "$WINDOWS"
  log "copy libnrsc5.dll into the installed gnuradio/hdradio folder, or set HDRADIO_NRSC5_LIB to its path"
  exit 0
fi

CMAKE_ARGS=(-DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_PREFIX_PATH="$PREFIX"
            -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_LIBDIR=lib)
case "$(uname -m)" in
  x86_64|amd64|i?86) CMAKE_ARGS+=(-DUSE_SSE=ON) ;;
esac
# A conda prefix has no libusb/libao unless asked; let nrsc5 build its own.
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

log "building into $PREFIX"
cmake -S "$SRC/nrsc5" -B "$SRC/build" "${CMAKE_ARGS[@]}" >/dev/null
cmake --build "$SRC/build" -j"$JOBS" >/dev/null
SUDO=""
if [ ! -w "$PREFIX" ] && [ "$(id -u)" -ne 0 ]; then SUDO=sudo; fi
$SUDO cmake --install "$SRC/build" >/dev/null
if command -v ldconfig >/dev/null 2>&1 && [ "$(uname -s)" = Linux ]; then
  $SUDO ldconfig 2>/dev/null || true
fi

log "installed: $("$PREFIX/bin/nrsc5" -v 2>&1 | head -1)"
log "library:   $(ls "$PREFIX"/lib/libnrsc5.* 2>/dev/null | head -1)"
