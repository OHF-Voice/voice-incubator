# okay_nabu: VPE vs. XVF3800, all sessions

True detections at cutoff 0.85 (VPE default sensitivity) with the VAD gate. Repeated sessions are listed in the order they were recorded: oknabu2, then the locked set xl, the fixed set xf, and overnight the xmos (xm), dual (xd) and second fixed (xf2) sets. Compare within one night: the room was ~6 dB quieter overnight (see the room noise table). TV-only cells are false accepts in ~10 min of TV.

## Reference wake word file (112 wake words)

| output | wake words only | TV -10 dB | TV 0 dB | TV only (false accepts) |
| --- | ---: | ---: | ---: | ---: |
| *reference: PE (VPE, NS x4)* | *43, 43, 46* | *34, 36* | *3, 4* | — |
| *reference: 3800 (ASR beam 0 x1)* | *96, 91, 105* | *87, 89* | *11, 14* | — |
| Source audio, scored directly (no playback) | 111 |  |  |  |
| VPE 1 AEC x1 | 62, 59, 57, 59, 58, 56 | 23, 23, 21, 20, 24, 20 | 1, 2, 2, 1, 1, 2 | 0, 0, 0, 0, 0, 0 |
| VPE 1 AEC x4 | 79, 83, 76, 86, 92, 91 | 24, 29, 28, 28, 29, 30 | 1, 2, 2, 3, 3, 4 | 0, 0, 0, 0, 0, 0 |
| VPE 1 NS x4 | 51, 47, 50, 58, 70, 65 | 35, 32, 32, 52, 58, 59 | 18, 17, 18, 17, 15, 15 | 0, 0, 0, 0, 0, 0 |
| VPE 2 AGC x1 | 66, 61, 57, 83, 92, 89 | 41, 36, 36, 54, 55, 54 | 15, 15, 16, 7, 7, 5 | 0, 0, 0, 0, 0, 0 |
| VPE 2 AGC x4 | 68, 67, 66, 92, 98, 98 | 44, 38, 35, 59, 52, 44 | 13, 13, 16, 5, 6, 6 | 0, 0, 0, 0, 0, 0 |
| VPE 2 NS x4 | 53, 50, 52, 72, 80, 77 | 37, 31, 35, 46, 45, 42 | 13, 11, 16, 6, 7, 6 | 0, 0, 0, 0, 0, 0 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | 102 | 38 | 7 | 0 |
| XVF3800 ASR, beam fixed at the wake words | 98, 104 | 79, 89 | 47, 55 | 0, 0 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | 109 | 55 | 13 | 0 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | 109 | 55 | 13 | 0 |
| XVF3800 ASR, focused beam 1 (default; locked on the TV) | 67 | 4 | 0 | 0 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | 38 | 0 | 0 | 0 |
| XVF3800 ASR, focused beam locked on the wake words | 95 | 48 | 8 | 0 |
| XVF3800 processed auto-select beam (6,3) | 93, 98 | 49, 43 | 4, 9 | 0, 0 |
| XVF3800 processed beam fixed at the wake words (6,0) | 92, 102 | 80, 89 | 57, 63 | 0, 0 |

## Office-multicap playlist (112 commands, 28 per speaker)

| output | wake words only | TV -10 dB | TV 0 dB |
| --- | ---: | ---: | ---: |
| Source audio, scored directly (no playback) | 47 |  |  |
| VPE 1 AEC x1 | 25, 24 | 4, 8 | 1, 2 |
| VPE 1 AEC x4 | 33, 33 | 5, 8 | 2, 2 |
| VPE 1 NS x4 | 11, 10 | 7, 6 | 3, 4 |
| VPE 2 AGC x1 | 8, 6 | 7, 4 | 3, 3 |
| VPE 2 AGC x4 | 4, 2 | 2, 1 | 0, 0 |
| VPE 2 NS x4 | 10, 10 | 8, 9 | 1, 1 |
| XVF3800 ASR, beam fixed at the wake words | 38 | 33 | 23 |
| XVF3800 ASR, focused beam locked on the wake words | 45 | 19 | 1 |
| XVF3800 processed auto-select beam (6,3) | 58 | 14 | 3 |
| XVF3800 processed beam fixed at the wake words (6,0) | 49 | 34 | 17 |

## Validated office playlist (104 commands the model detects directly: 28 jenn, 28 mike, 28 natalie, 20 oliver)

| output | wake words only | TV -10 dB | TV 0 dB |
| --- | ---: | ---: | ---: |
| Source audio, scored directly (no playback) | 95 |  |  |
| VPE 1 AEC x1 | 31, 34, 36, 39 | 13, 13, 14, 13 | 0, 1, 1, 1 |
| VPE 1 AEC x4 | 45, 62, 65, 63 | 13, 17, 17, 16 | 1, 1, 2, 3 |
| VPE 1 NS x4 | 11, 20, 24, 26 | 11, 22, 26, 28 | 4, 7, 4, 7 |
| VPE 2 AGC x1 | 16, 29, 29, 27 | 9, 18, 18, 17 | 1, 0, 1, 1 |
| VPE 2 AGC x4 | 10, 21, 26, 23 | 6, 4, 6, 4 | 1, 0, 0, 0 |
| VPE 2 NS x4 | 14, 23, 22, 21 | 9, 17, 14, 17 | 2, 3, 2, 3 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | 81 | 20 | 1 |
| XVF3800 ASR, beam fixed at the wake words | 86 | 66 | 47 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | 84 | 48 | 3 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | 83 | 48 | 3 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | 18 | 1 | 0 |
| XVF3800 ASR, focused beam locked on the wake words | 61 | 29 | 2 |
| XVF3800 processed auto-select beam (6,3) | 75, 86 | 19, 35 | 1, 4 |
| XVF3800 processed beam fixed at the wake words (6,0) | 85 | 75 | 48 |

## Wake Word Collective playlist (112 validated: 28 each English, German, Dutch/French, other)

| output | wake words only | TV -10 dB | TV 0 dB |
| --- | ---: | ---: | ---: |
| Source audio, scored directly (no playback) | 111 |  |  |
| VPE 1 AEC x1 | 86, 90, 86 | 46, 45, 45 | 7, 5, 8 |
| VPE 1 AEC x4 | 100, 103, 100 | 49, 49, 51 | 8, 6, 7 |
| VPE 1 NS x4 | 90, 83, 87 | 83, 80, 77 | 27, 29, 28 |
| VPE 2 AGC x1 | 97, 98, 96 | 76, 78, 76 | 18, 18, 18 |
| VPE 2 AGC x4 | 97, 96, 94 | 75, 74, 70 | 9, 12, 8 |
| VPE 2 NS x4 | 90, 91, 92 | 74, 75, 73 | 17, 18, 20 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | 108 | 58 | 6 |
| XVF3800 ASR, beam fixed at the wake words | 109 | 103 | 81 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | 110 | 93 | 16 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | 110 | 93 | 16 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | 58 | 2 | 0 |
| XVF3800 processed auto-select beam (6,3) | 108 | 53 | 7 |
| XVF3800 processed beam fixed at the wake words (6,0) | 108 | 100 | 78 |

## Synthetic positives playlist (112 validated: 14 per TTS language)

| output | wake words only | TV -10 dB | TV 0 dB |
| --- | ---: | ---: | ---: |
| Source audio, scored directly (no playback) | 111 |  |  |
| VPE 1 AEC x1 | 69, 59, 69 | 23, 27, 27 | 2, 1, 1 |
| VPE 1 AEC x4 | 96, 92, 93 | 28, 33, 31 | 2, 2, 3 |
| VPE 1 NS x4 | 71, 69, 76 | 55, 59, 59 | 12, 12, 13 |
| VPE 2 AGC x1 | 88, 87, 87 | 54, 49 (1 FA), 56 | 7, 10, 7 |
| VPE 2 AGC x4 | 90, 92, 95 | 53, 47, 52 | 7, 7, 7 |
| VPE 2 NS x4 | 79, 71, 75 | 45, 49 (1 FA), 44 | 3, 6, 4 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | 106 | 38 | 3 |
| XVF3800 ASR, beam fixed at the wake words | 107 | 91 | 62 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | 104 | 65 | 8 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | 104 | 65 | 8 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | 24 | 2 | 0 |
| XVF3800 processed auto-select beam (6,3) | 105 | 38 | 4 |
| XVF3800 processed beam fixed at the wake words (6,0) | 104 | 90 | 63 |

## Office-multicap playlist (112 commands, 28 per speaker), by speaker

| output | condition | jenn | mike | natalie | oliver |
| --- | --- | ---: | ---: | ---: | ---: |
| Source audio, scored directly (no playback) | wake words only | 24/28 | 12/28 | 6/28 | 5/28 |
| VPE 1 AEC x1 | wake words only | 16/28, 16/28 | 9/28, 8/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 1 AEC x1 | TV -10 dB | 3/28, 5/28 | 1/28, 3/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 1 AEC x1 | TV 0 dB | 1/28, 2/28 | 0/28, 0/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 1 AEC x4 | wake words only | 21/28, 19/28 | 10/28, 11/28 | 2/28, 2/28 | 0/28, 1/28 |
| VPE 1 AEC x4 | TV -10 dB | 4/28, 5/28 | 1/28, 3/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 1 AEC x4 | TV 0 dB | 1/28, 2/28 | 1/28, 0/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 1 NS x4 | wake words only | 8/28, 7/28 | 2/28, 2/28 | 0/28, 0/28 | 1/28, 1/28 |
| VPE 1 NS x4 | TV -10 dB | 5/28, 4/28 | 1/28, 2/28 | 1/28, 0/28 | 0/28, 0/28 |
| VPE 1 NS x4 | TV 0 dB | 2/28, 4/28 | 0/28, 0/28 | 1/28, 0/28 | 0/28, 0/28 |
| VPE 2 AGC x1 | wake words only | 6/28, 5/28 | 2/28, 1/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 2 AGC x1 | TV -10 dB | 5/28, 3/28 | 2/28, 1/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 2 AGC x1 | TV 0 dB | 1/28, 2/28 | 2/28, 1/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 2 AGC x4 | wake words only | 0/28, 0/28 | 4/28, 2/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 2 AGC x4 | TV -10 dB | 0/28, 1/28 | 2/28, 0/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 2 AGC x4 | TV 0 dB | 0/28, 0/28 | 0/28, 0/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 2 NS x4 | wake words only | 8/28, 8/28 | 2/28, 1/28 | 0/28, 0/28 | 0/28, 1/28 |
| VPE 2 NS x4 | TV -10 dB | 7/28, 8/28 | 1/28, 1/28 | 0/28, 0/28 | 0/28, 0/28 |
| VPE 2 NS x4 | TV 0 dB | 0/28, 0/28 | 1/28, 1/28 | 0/28, 0/28 | 0/28, 0/28 |
| XVF3800 ASR, beam fixed at the wake words | wake words only | 19/28 | 13/28 | 5/28 | 1/28 |
| XVF3800 ASR, beam fixed at the wake words | TV -10 dB | 18/28 | 13/28 | 2/28 | 0/28 |
| XVF3800 ASR, beam fixed at the wake words | TV 0 dB | 15/28 | 7/28 | 1/28 | 0/28 |
| XVF3800 ASR, focused beam locked on the wake words | wake words only | 21/28 | 16/28 | 6/28 | 2/28 |
| XVF3800 ASR, focused beam locked on the wake words | TV -10 dB | 14/28 | 5/28 | 0/28 | 0/28 |
| XVF3800 ASR, focused beam locked on the wake words | TV 0 dB | 1/28 | 0/28 | 0/28 | 0/28 |
| XVF3800 processed auto-select beam (6,3) | wake words only | 22/28 | 22/28 | 7/28 | 7/28 |
| XVF3800 processed auto-select beam (6,3) | TV -10 dB | 8/28 | 5/28 | 1/28 | 0/28 |
| XVF3800 processed auto-select beam (6,3) | TV 0 dB | 2/28 | 1/28 | 0/28 | 0/28 |
| XVF3800 processed beam fixed at the wake words (6,0) | wake words only | 23/28 | 20/28 | 4/28 | 2/28 |
| XVF3800 processed beam fixed at the wake words (6,0) | TV -10 dB | 20/28 | 12/28 | 2/28 | 0/28 |
| XVF3800 processed beam fixed at the wake words (6,0) | TV 0 dB | 12/28 | 4/28 | 1/28 | 0/28 |

## Validated office playlist (104 commands the model detects directly: 28 jenn, 28 mike, 28 natalie, 20 oliver), by speaker

| output | condition | jenn | mike | natalie | oliver |
| --- | --- | ---: | ---: | ---: | ---: |
| Source audio, scored directly (no playback) | wake words only | 27/28 | 26/28 | 25/28 | 17/20 |
| VPE 1 AEC x1 | wake words only | 18/28, 17/28, 18/28, 17/28 | 10/28, 11/28, 12/28, 12/28 | 3/28, 5/28, 5/28, 8/28 | 0/20, 1/20, 1/20, 2/20 |
| VPE 1 AEC x1 | TV -10 dB | 9/28, 10/28, 11/28, 11/28 | 3/28, 2/28, 2/28, 1/28 | 1/28, 1/28, 1/28, 1/28 | 0/20, 0/20, 0/20, 0/20 |
| VPE 1 AEC x1 | TV 0 dB | 0/28, 0/28, 0/28, 0/28 | 0/28, 1/28, 1/28, 1/28 | 0/28, 0/28, 0/28, 0/28 | 0/20, 0/20, 0/20, 0/20 |
| VPE 1 AEC x4 | wake words only | 22/28, 27/28, 24/28, 25/28 | 15/28, 19/28, 19/28, 17/28 | 7/28, 13/28, 19/28, 17/28 | 1/20, 3/20, 3/20, 4/20 |
| VPE 1 AEC x4 | TV -10 dB | 9/28, 12/28, 12/28, 12/28 | 3/28, 3/28, 3/28, 2/28 | 1/28, 2/28, 2/28, 2/28 | 0/20, 0/20, 0/20, 0/20 |
| VPE 1 AEC x4 | TV 0 dB | 1/28, 1/28, 2/28, 3/28 | 0/28, 0/28, 0/28, 0/28 | 0/28, 0/28, 0/28, 0/28 | 0/20, 0/20, 0/20, 0/20 |
| VPE 1 NS x4 | wake words only | 7/28, 9/28, 12/28, 10/28 | 1/28, 7/28, 9/28, 10/28 | 2/28, 2/28, 3/28, 4/28 | 1/20, 2/20, 0/20, 2/20 |
| VPE 1 NS x4 | TV -10 dB | 7/28, 9/28, 10/28, 10/28 | 2/28, 4/28, 7/28, 9/28 | 1/28, 6/28, 8/28, 7/28 | 1/20, 3/20, 1/20, 2/20 |
| VPE 1 NS x4 | TV 0 dB | 2/28, 3/28, 2/28, 4/28 | 1/28, 2/28, 2/28, 1/28 | 1/28, 1/28, 0/28, 1/28 | 0/20, 1/20, 0/20, 1/20 |
| VPE 2 AGC x1 | wake words only | 7/28, 15/28, 13/28, 13/28 | 7/28, 8/28, 10/28, 8/28 | 2/28, 2/28, 3/28, 4/28 | 0/20, 4/20, 3/20, 2/20 |
| VPE 2 AGC x1 | TV -10 dB | 4/28, 5/28, 7/28, 6/28 | 4/28, 7/28, 8/28, 6/28 | 1/28, 5/28, 3/28, 4/28 | 0/20, 1/20, 0/20, 1/20 |
| VPE 2 AGC x1 | TV 0 dB | 1/28, 0/28, 1/28, 0/28 | 0/28, 0/28, 0/28, 0/28 | 0/28, 0/28, 0/28, 0/28 | 0/20, 0/20, 0/20, 1/20 |
| VPE 2 AGC x4 | wake words only | 1/28, 6/28, 6/28, 5/28 | 7/28, 11/28, 13/28, 11/28 | 2/28, 0/28, 3/28, 4/28 | 0/20, 4/20, 4/20, 3/20 |
| VPE 2 AGC x4 | TV -10 dB | 1/28, 2/28, 3/28, 1/28 | 4/28, 1/28, 2/28, 2/28 | 1/28, 1/28, 1/28, 1/28 | 0/20, 0/20, 0/20, 0/20 |
| VPE 2 AGC x4 | TV 0 dB | 1/28, 0/28, 0/28, 0/28 | 0/28, 0/28, 0/28, 0/28 | 0/28, 0/28, 0/28, 0/28 | 0/20, 0/20, 0/20, 0/20 |
| VPE 2 NS x4 | wake words only | 8/28, 13/28, 12/28, 12/28 | 5/28, 5/28, 7/28, 8/28 | 1/28, 3/28, 1/28, 0/28 | 0/20, 2/20, 2/20, 1/20 |
| VPE 2 NS x4 | TV -10 dB | 3/28, 5/28, 5/28, 6/28 | 4/28, 7/28, 6/28, 4/28 | 1/28, 4/28, 3/28, 6/28 | 1/20, 1/20, 0/20, 1/20 |
| VPE 2 NS x4 | TV 0 dB | 1/28, 1/28, 2/28, 2/28 | 1/28, 2/28, 0/28, 0/28 | 0/28, 0/28, 0/28, 0/28 | 0/20, 0/20, 0/20, 1/20 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | wake words only | 27/28 | 21/28 | 20/28 | 13/20 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | TV -10 dB | 10/28 | 5/28 | 4/28 | 1/20 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | TV 0 dB | 1/28 | 0/28 | 0/28 | 0/20 |
| XVF3800 ASR, beam fixed at the wake words | wake words only | 27/28 | 24/28 | 24/28 | 11/20 |
| XVF3800 ASR, beam fixed at the wake words | TV -10 dB | 24/28 | 15/28 | 19/28 | 8/20 |
| XVF3800 ASR, beam fixed at the wake words | TV 0 dB | 19/28 | 11/28 | 14/28 | 3/20 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | wake words only | 25/28 | 25/28 | 21/28 | 13/20 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | TV -10 dB | 21/28 | 12/28 | 11/28 | 4/20 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | TV 0 dB | 3/28 | 0/28 | 0/28 | 0/20 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | wake words only | 25/28 | 25/28 | 21/28 | 12/20 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | TV -10 dB | 21/28 | 12/28 | 11/28 | 4/20 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | TV 0 dB | 3/28 | 0/28 | 0/28 | 0/20 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | wake words only | 14/28 | 2/28 | 1/28 | 1/20 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | TV -10 dB | 1/28 | 0/28 | 0/28 | 0/20 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | TV 0 dB | 0/28 | 0/28 | 0/28 | 0/20 |
| XVF3800 ASR, focused beam locked on the wake words | wake words only | 22/28 | 18/28 | 16/28 | 5/20 |
| XVF3800 ASR, focused beam locked on the wake words | TV -10 dB | 19/28 | 7/28 | 2/28 | 1/20 |
| XVF3800 ASR, focused beam locked on the wake words | TV 0 dB | 2/28 | 0/28 | 0/28 | 0/20 |
| XVF3800 processed auto-select beam (6,3) | wake words only | 26/28, 27/28 | 24/28, 26/28 | 15/28, 19/28 | 10/20, 14/20 |
| XVF3800 processed auto-select beam (6,3) | TV -10 dB | 10/28, 15/28 | 4/28, 8/28 | 4/28, 6/28 | 1/20, 6/20 |
| XVF3800 processed auto-select beam (6,3) | TV 0 dB | 1/28, 3/28 | 0/28, 1/28 | 0/28, 0/28 | 0/20, 0/20 |
| XVF3800 processed beam fixed at the wake words (6,0) | wake words only | 26/28 | 22/28 | 23/28 | 14/20 |
| XVF3800 processed beam fixed at the wake words (6,0) | TV -10 dB | 26/28 | 19/28 | 19/28 | 11/20 |
| XVF3800 processed beam fixed at the wake words (6,0) | TV 0 dB | 19/28 | 12/28 | 12/28 | 5/20 |

## Wake Word Collective playlist (112 validated: 28 each English, German, Dutch/French, other), by speaker

| output | condition | English | German | Dutch/French | other |
| --- | --- | ---: | ---: | ---: | ---: |
| Source audio, scored directly (no playback) | wake words only | 27/28 | 28/28 | 28/28 | 28/28 |
| VPE 1 AEC x1 | wake words only | 19/28, 21/28, 19/28 | 24/28, 25/28, 22/28 | 23/28, 24/28, 23/28 | 20/28, 20/28, 22/28 |
| VPE 1 AEC x1 | TV -10 dB | 9/28, 8/28, 8/28 | 12/28, 13/28, 13/28 | 12/28, 11/28, 12/28 | 13/28, 13/28, 12/28 |
| VPE 1 AEC x1 | TV 0 dB | 2/28, 1/28, 2/28 | 1/28, 1/28, 2/28 | 1/28, 1/28, 1/28 | 3/28, 2/28, 3/28 |
| VPE 1 AEC x4 | wake words only | 23/28, 25/28, 24/28 | 26/28, 26/28, 26/28 | 26/28, 25/28, 25/28 | 25/28, 27/28, 25/28 |
| VPE 1 AEC x4 | TV -10 dB | 10/28, 9/28, 10/28 | 14/28, 15/28, 15/28 | 12/28, 11/28, 12/28 | 13/28, 14/28, 14/28 |
| VPE 1 AEC x4 | TV 0 dB | 1/28, 1/28, 1/28 | 2/28, 2/28, 1/28 | 1/28, 1/28, 2/28 | 4/28, 2/28, 3/28 |
| VPE 1 NS x4 | wake words only | 20/28, 20/28, 23/28 | 22/28, 19/28, 21/28 | 24/28, 23/28, 23/28 | 24/28, 21/28, 20/28 |
| VPE 1 NS x4 | TV -10 dB | 22/28, 20/28, 19/28 | 21/28, 19/28, 19/28 | 20/28, 19/28, 19/28 | 20/28, 22/28, 20/28 |
| VPE 1 NS x4 | TV 0 dB | 5/28, 5/28, 5/28 | 8/28, 9/28, 9/28 | 8/28, 8/28, 7/28 | 6/28, 7/28, 7/28 |
| VPE 2 AGC x1 | wake words only | 23/28, 22/28, 22/28 | 23/28, 25/28, 25/28 | 25/28, 24/28, 24/28 | 26/28, 27/28, 25/28 |
| VPE 2 AGC x1 | TV -10 dB | 19/28, 18/28, 16/28 | 18/28, 19/28, 21/28 | 17/28, 20/28, 19/28 | 22/28, 21/28, 20/28 |
| VPE 2 AGC x1 | TV 0 dB | 4/28, 4/28, 4/28 | 7/28, 8/28, 8/28 | 4/28, 3/28, 2/28 | 3/28, 3/28, 4/28 |
| VPE 2 AGC x4 | wake words only | 22/28, 22/28, 19/28 | 25/28, 25/28, 25/28 | 24/28, 24/28, 24/28 | 26/28, 25/28, 26/28 |
| VPE 2 AGC x4 | TV -10 dB | 17/28, 14/28, 13/28 | 19/28, 21/28, 21/28 | 16/28, 16/28, 18/28 | 23/28, 23/28, 18/28 |
| VPE 2 AGC x4 | TV 0 dB | 2/28, 2/28, 1/28 | 3/28, 4/28, 4/28 | 1/28, 2/28, 1/28 | 3/28, 4/28, 2/28 |
| VPE 2 NS x4 | wake words only | 21/28, 22/28, 22/28 | 22/28, 23/28, 23/28 | 24/28, 23/28, 22/28 | 23/28, 23/28, 25/28 |
| VPE 2 NS x4 | TV -10 dB | 18/28, 18/28, 15/28 | 18/28, 18/28, 18/28 | 17/28, 18/28, 19/28 | 21/28, 21/28, 21/28 |
| VPE 2 NS x4 | TV 0 dB | 4/28, 3/28, 4/28 | 7/28, 9/28, 8/28 | 3/28, 1/28, 4/28 | 3/28, 5/28, 4/28 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | wake words only | 25/28 | 28/28 | 27/28 | 28/28 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | TV -10 dB | 13/28 | 15/28 | 14/28 | 16/28 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | TV 0 dB | 0/28 | 3/28 | 1/28 | 2/28 |
| XVF3800 ASR, beam fixed at the wake words | wake words only | 27/28 | 27/28 | 27/28 | 28/28 |
| XVF3800 ASR, beam fixed at the wake words | TV -10 dB | 25/28 | 26/28 | 24/28 | 28/28 |
| XVF3800 ASR, beam fixed at the wake words | TV 0 dB | 21/28 | 20/28 | 20/28 | 20/28 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | wake words only | 27/28 | 28/28 | 27/28 | 28/28 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | TV -10 dB | 22/28 | 24/28 | 22/28 | 25/28 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | TV 0 dB | 1/28 | 6/28 | 3/28 | 6/28 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | wake words only | 27/28 | 28/28 | 27/28 | 28/28 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | TV -10 dB | 22/28 | 24/28 | 22/28 | 25/28 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | TV 0 dB | 1/28 | 6/28 | 3/28 | 6/28 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | wake words only | 8/28 | 14/28 | 18/28 | 18/28 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | TV -10 dB | 0/28 | 1/28 | 0/28 | 1/28 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | TV 0 dB | 0/28 | 0/28 | 0/28 | 0/28 |
| XVF3800 processed auto-select beam (6,3) | wake words only | 26/28 | 27/28 | 27/28 | 28/28 |
| XVF3800 processed auto-select beam (6,3) | TV -10 dB | 12/28 | 15/28 | 12/28 | 14/28 |
| XVF3800 processed auto-select beam (6,3) | TV 0 dB | 0/28 | 3/28 | 1/28 | 3/28 |
| XVF3800 processed beam fixed at the wake words (6,0) | wake words only | 27/28 | 26/28 | 27/28 | 28/28 |
| XVF3800 processed beam fixed at the wake words (6,0) | TV -10 dB | 24/28 | 24/28 | 25/28 | 27/28 |
| XVF3800 processed beam fixed at the wake words (6,0) | TV 0 dB | 20/28 | 19/28 | 21/28 | 18/28 |

## Synthetic positives playlist (112 validated: 14 per TTS language), by speaker

| output | condition | ca | de | en | en-female | es | fr | it | nl |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Source audio, scored directly (no playback) | wake words only | 14/14 | 14/14 | 14/14 | 14/14 | 13/14 | 14/14 | 14/14 | 14/14 |
| VPE 1 AEC x1 | wake words only | 8/14, 6/14, 7/14 | 9/14, 8/14, 10/14 | 11/14, 11/14, 12/14 | 9/14, 8/14, 9/14 | 7/14, 4/14, 4/14 | 11/14, 10/14, 11/14 | 6/14, 6/14, 7/14 | 8/14, 6/14, 9/14 |
| VPE 1 AEC x1 | TV -10 dB | 1/14, 2/14, 2/14 | 3/14, 4/14, 3/14 | 5/14, 5/14, 5/14 | 6/14, 6/14, 6/14 | 2/14, 2/14, 2/14 | 2/14, 3/14, 3/14 | 3/14, 2/14, 3/14 | 1/14, 3/14, 3/14 |
| VPE 1 AEC x1 | TV 0 dB | 0/14, 0/14, 0/14 | 1/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 1/14, 1/14, 1/14 |
| VPE 1 AEC x4 | wake words only | 12/14, 11/14, 12/14 | 12/14, 12/14, 12/14 | 14/14, 13/14, 13/14 | 13/14, 11/14, 13/14 | 11/14, 10/14, 10/14 | 13/14, 13/14, 13/14 | 9/14, 9/14, 8/14 | 12/14, 13/14, 12/14 |
| VPE 1 AEC x4 | TV -10 dB | 2/14, 3/14, 3/14 | 5/14, 6/14, 4/14 | 5/14, 4/14, 5/14 | 7/14, 8/14, 7/14 | 1/14, 2/14, 2/14 | 3/14, 4/14, 3/14 | 3/14, 2/14, 3/14 | 2/14, 4/14, 4/14 |
| VPE 1 AEC x4 | TV 0 dB | 0/14, 0/14, 0/14 | 1/14, 1/14, 2/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 1/14, 1/14, 1/14 |
| VPE 1 NS x4 | wake words only | 7/14, 6/14, 7/14 | 9/14, 9/14, 9/14 | 12/14, 10/14, 12/14 | 12/14, 13/14, 12/14 | 5/14, 6/14, 7/14 | 11/14, 11/14, 12/14 | 6/14, 5/14, 9/14 | 9/14, 9/14, 8/14 |
| VPE 1 NS x4 | TV -10 dB | 3/14, 7/14, 5/14 | 8/14, 8/14, 8/14 | 9/14, 10/14, 10/14 | 13/14, 11/14, 12/14 | 5/14, 6/14, 6/14 | 7/14, 7/14, 9/14 | 5/14, 4/14, 4/14 | 5/14, 6/14, 5/14 |
| VPE 1 NS x4 | TV 0 dB | 1/14, 0/14, 1/14 | 4/14, 4/14, 4/14 | 1/14, 1/14, 1/14 | 2/14, 3/14, 3/14 | 1/14, 1/14, 1/14 | 1/14, 1/14, 1/14 | 1/14, 1/14, 1/14 | 1/14, 1/14, 1/14 |
| VPE 2 AGC x1 | wake words only | 12/14, 9/14, 11/14 | 10/14, 11/14, 10/14 | 13/14, 13/14, 13/14 | 12/14, 12/14, 13/14 | 7/14, 7/14, 8/14 | 12/14, 12/14, 12/14 | 12/14, 11/14, 9/14 | 10/14, 12/14, 11/14 |
| VPE 2 AGC x1 | TV -10 dB | 6/14, 6/14, 7/14 | 7/14, 7/14, 7/14 | 5/14, 5/14, 7/14 | 10/14, 9/14, 9/14 | 3/14, 5/14, 4/14 | 8/14, 5/14, 8/14 | 8/14, 4/14, 6/14 | 7/14, 8/14, 8/14 |
| VPE 2 AGC x1 | TV 0 dB | 0/14, 1/14, 1/14 | 3/14, 3/14, 3/14 | 0/14, 1/14, 0/14 | 1/14, 2/14, 1/14 | 0/14, 1/14, 0/14 | 0/14, 0/14, 0/14 | 2/14, 1/14, 1/14 | 1/14, 1/14, 1/14 |
| VPE 2 AGC x4 | wake words only | 11/14, 10/14, 12/14 | 11/14, 12/14, 12/14 | 14/14, 13/14, 13/14 | 12/14, 12/14, 13/14 | 9/14, 9/14, 10/14 | 11/14, 13/14, 13/14 | 12/14, 11/14, 10/14 | 10/14, 12/14, 12/14 |
| VPE 2 AGC x4 | TV -10 dB | 4/14, 5/14, 5/14 | 7/14, 7/14, 8/14 | 6/14, 7/14, 6/14 | 10/14, 10/14, 10/14 | 6/14, 3/14, 3/14 | 6/14, 3/14, 7/14 | 7/14, 4/14, 6/14 | 7/14, 8/14, 7/14 |
| VPE 2 AGC x4 | TV 0 dB | 0/14, 0/14, 0/14 | 3/14, 3/14, 3/14 | 0/14, 0/14, 0/14 | 1/14, 1/14, 1/14 | 0/14, 1/14, 0/14 | 1/14, 0/14, 1/14 | 1/14, 1/14, 1/14 | 1/14, 1/14, 1/14 |
| VPE 2 NS x4 | wake words only | 9/14, 6/14, 10/14 | 10/14, 9/14, 10/14 | 12/14, 11/14, 11/14 | 11/14, 10/14, 12/14 | 8/14, 5/14, 5/14 | 11/14, 11/14, 11/14 | 9/14, 8/14, 6/14 | 9/14, 11/14, 10/14 |
| VPE 2 NS x4 | TV -10 dB | 3/14, 4/14, 4/14 | 7/14, 7/14, 7/14 | 7/14, 7/14, 7/14 | 9/14, 9/14, 8/14 | 3/14, 4/14, 2/14 | 6/14, 6/14, 6/14 | 4/14, 5/14, 3/14 | 6/14, 7/14, 7/14 |
| VPE 2 NS x4 | TV 0 dB | 0/14, 2/14, 0/14 | 1/14, 1/14, 2/14 | 0/14, 1/14, 0/14 | 1/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 0/14, 0/14 | 0/14, 1/14, 1/14 | 1/14, 1/14, 1/14 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | wake words only | 13/14 | 12/14 | 14/14 | 14/14 | 14/14 | 14/14 | 11/14 | 14/14 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | TV -10 dB | 3/14 | 7/14 | 5/14 | 8/14 | 2/14 | 5/14 | 3/14 | 5/14 |
| XVF3800 ASR, auto-select beam (7,3; XMOS's recommendation) | TV 0 dB | 0/14 | 2/14 | 0/14 | 0/14 | 0/14 | 0/14 | 0/14 | 1/14 |
| XVF3800 ASR, beam fixed at the wake words | wake words only | 14/14 | 12/14 | 14/14 | 14/14 | 13/14 | 14/14 | 12/14 | 14/14 |
| XVF3800 ASR, beam fixed at the wake words | TV -10 dB | 9/14 | 12/14 | 12/14 | 14/14 | 10/14 | 12/14 | 11/14 | 11/14 |
| XVF3800 ASR, beam fixed at the wake words | TV 0 dB | 4/14 | 9/14 | 11/14 | 14/14 | 5/14 | 7/14 | 5/14 | 7/14 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | wake words only | 12/14 | 12/14 | 14/14 | 14/14 | 13/14 | 14/14 | 11/14 | 14/14 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | TV -10 dB | 5/14 | 11/14 | 10/14 | 11/14 | 5/14 | 10/14 | 6/14 | 7/14 |
| XVF3800 ASR, either focused beam (7,0 or 7,1) | TV 0 dB | 0/14 | 4/14 | 0/14 | 1/14 | 1/14 | 0/14 | 1/14 | 1/14 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | wake words only | 12/14 | 12/14 | 14/14 | 14/14 | 13/14 | 14/14 | 11/14 | 14/14 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | TV -10 dB | 5/14 | 11/14 | 10/14 | 11/14 | 5/14 | 10/14 | 6/14 | 7/14 |
| XVF3800 ASR, focused beam 1 (7,0), unprimed | TV 0 dB | 0/14 | 4/14 | 0/14 | 1/14 | 1/14 | 0/14 | 1/14 | 1/14 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | wake words only | 1/14 | 6/14 | 5/14 | 0/14 | 0/14 | 3/14 | 3/14 | 6/14 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | TV -10 dB | 0/14 | 1/14 | 0/14 | 0/14 | 0/14 | 0/14 | 0/14 | 1/14 |
| XVF3800 ASR, focused beam 2 (7,1), unprimed | TV 0 dB | 0/14 | 0/14 | 0/14 | 0/14 | 0/14 | 0/14 | 0/14 | 0/14 |
| XVF3800 processed auto-select beam (6,3) | wake words only | 14/14 | 13/14 | 14/14 | 14/14 | 13/14 | 12/14 | 12/14 | 13/14 |
| XVF3800 processed auto-select beam (6,3) | TV -10 dB | 1/14 | 7/14 | 7/14 | 11/14 | 3/14 | 4/14 | 1/14 | 4/14 |
| XVF3800 processed auto-select beam (6,3) | TV 0 dB | 0/14 | 1/14 | 1/14 | 1/14 | 0/14 | 0/14 | 0/14 | 1/14 |
| XVF3800 processed beam fixed at the wake words (6,0) | wake words only | 12/14 | 12/14 | 14/14 | 14/14 | 13/14 | 13/14 | 12/14 | 14/14 |
| XVF3800 processed beam fixed at the wake words (6,0) | TV -10 dB | 9/14 | 12/14 | 12/14 | 14/14 | 10/14 | 12/14 | 11/14 | 10/14 |
| XVF3800 processed beam fixed at the wake words (6,0) | TV 0 dB | 4/14 | 9/14 | 11/14 | 14/14 | 4/14 | 8/14 | 6/14 | 7/14 |

## Room noise before playback (VPE 1 AEC, dBFS; room_noise.py)

| set | sessions | median | range |
| --- | ---: | ---: | --- |
| oknabu2 | 4 | -79.7 | -80.4 to -79.6 |
| xl | 10 | -80.1 | -80.6 to -78.4 |
| xf | 7 | -76.6 | -79.8 to -70.7 |
| xm | 13 | -86.4 | -88.0 to -83.6 |
| xd | 13 | -86.8 | -87.7 to -85.6 |
| xf2 | 13 | -87.2 | -87.8 to -86.7 |

