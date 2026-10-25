`timer_finished.flac` is from the [Home Assistant Voice Preview Edition
sounds](https://github.com/esphome/home-assistant-voice-pe/tree/dev/sounds),
© 2024 by [Clayton Charles Tapp](https://www.cctaudio.com/), licensed under
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

`thinking.flac` is made from `center_button_long_press.flac` from the same
sounds, by the same author and under the same licence, changed with sox:
trimmed, low-passed, given a tremolo, faded in and out, and made quieter:

    sox center_button_long_press.flac thinking.flac trim 0.1 1.3 lowpass 1500 \
        tremolo 6 60 fade t 0.15 1.2 0.3 norm -12
