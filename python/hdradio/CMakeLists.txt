# SPDX-License-Identifier: GPL-3.0-or-later
set(HDRADIO_PY __init__.py nrsc5.py radio.py hd_decoder.py hd_receiver.py
               hd_file_sink.py)
install(FILES ${HDRADIO_PY} DESTINATION ${GR_PYTHON_DIR}/gnuradio/hdradio)

# Tests import the package from the source tree. qa_hd_receiver decodes a
# real capture when HDRADIO_SAMPLE names one, and skips that part otherwise.
foreach(qa qa_hd_decoder qa_hd_file_sink qa_hd_receiver)
    add_test(NAME ${qa}
             COMMAND ${PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/${qa}.py)
    set_tests_properties(${qa} PROPERTIES
        ENVIRONMENT "PYTHONPATH=${PROJECT_SOURCE_DIR}/python")
endforeach()
