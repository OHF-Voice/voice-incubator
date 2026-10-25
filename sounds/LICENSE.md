`timer_finished.flac` is from the [Home Assistant Voice Preview Edition
sounds](https://github.com/esphome/home-assistant-voice-pe/tree/dev/sounds),
© 2024 by [Clayton Charles Tapp](https://www.cctaudio.com/), licensed under
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

`thinking.flac` is made from `center_button_long_press.flac` from the same
sounds, by the same author and under the same licence, changed with sox:
trimmed, an octave up, low-passed, compressed (8:1, evening out its decay),
given a tremolo, compressed gently again (2:1, slowly, raising its dips) and
faded in and out:

    sox center_button_long_press.flac thinking.flac trim 0.1 1.3 pitch 1200 \
        lowpass 3000 compand 0.02,0.2 -80,-80,-45,-45,0,-39 tremolo 6 60 \
        compand 0.03,0.12 -80,-80,-30,-30,0,-15 fade t 0.15 1.2 0.3 gain -n -1.5
