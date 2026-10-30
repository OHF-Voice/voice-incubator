# microWakeWord, vendored

Kevin Ahrendt's [microWakeWord](https://github.com/kahrendt/microWakeWord),
under the Apache License 2.0 (`LICENSE`), as voice_labs trains wake words with
it (`voice_labs/wake_trainer.py`, run with this folder's parent on its
`PYTHONPATH`): the `microwakeword` package of
[OHF-Voice/micro-wake-word](https://github.com/OHF-Voice/micro-wake-word) at
`4665173` ("Pin dependencies"), with

- its `clips-validation-modes` branch merged (validation and testing from
  clip sets, training draws in parallel),
- focal loss behind a config switch, and the calibration spectrogram trimmed
  to the stride (`b090c74`),
- and the working tree's uncommitted changes on top, as the app trained with
  them: a room simulation in place of the background noise and impulse
  response (`audio/room_simulation.py`, `audio/augmentation.py`); the audio
  stack imported only where clips are generated, so a run on spectrograms
  needs none of it (`data.py`, `test.py`); soft targets for distillation and
  a cap on the training split (`data.py`); freezing through a layer and a
  pooling stride, for fine-tuning (`mixednet.py`, `train.py`,
  `model_train_eval.py`).

Its dependencies for training are in `requirements-training.txt`; its audio
stack, TensorBoard and evaluation are left out of a training run.
