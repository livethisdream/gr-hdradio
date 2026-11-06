# Copyright 2026 Neil Rogers.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# gr-hdradio is Python only, so this needs CMake and GNU Radio's Python but
# no compiler. libnrsc5 is found at run time; build it with
# scripts/install_nrsc5.sh.
#
#   cmake -S . -B build -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX
#   cmake --build build && cmake --install build && ctest --test-dir build
#
# Options (both default from the Python that runs GNU Radio):
#   PYTHON_EXECUTABLE   that Python
#   GR_PYTHON_DIR       where the gnuradio package lives, for example
#                       /usr/lib/python3/dist-packages on Debian and Ubuntu

cmake_minimum_required(VERSION 3.16)
project(gr-hdradio VERSION 0.1.0 LANGUAGES NONE)
enable_testing()

if(NOT PYTHON_EXECUTABLE)
    find_program(PYTHON_EXECUTABLE NAMES python3 python REQUIRED)
endif()
execute_process(
    COMMAND ${PYTHON_EXECUTABLE} -c "import gnuradio.gr as g, os; print(os.path.dirname(os.path.dirname(os.path.dirname(g.__file__))))"
    OUTPUT_VARIABLE _GR_SITE OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE _GR_FOUND)
if(NOT _GR_FOUND EQUAL 0)
    message(FATAL_ERROR "${PYTHON_EXECUTABLE} cannot import GNU Radio; pass -DPYTHON_EXECUTABLE=")
endif()
if(NOT GR_PYTHON_DIR)
    set(GR_PYTHON_DIR ${_GR_SITE})
endif()
set(GR_GRC_BLOCKS_DIR share/gnuradio/grc/blocks CACHE STRING "GRC block YAML folder, under the prefix")
message(STATUS "gr-hdradio: Python package to ${GR_PYTHON_DIR}/gnuradio/hdradio")
message(STATUS "gr-hdradio: GRC blocks to ${CMAKE_INSTALL_PREFIX}/${GR_GRC_BLOCKS_DIR}")

add_subdirectory(python/hdradio)
add_subdirectory(grc)
