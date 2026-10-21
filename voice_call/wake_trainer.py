"""Train a wake word's microWakeWord model: warm-started, temperature-scaled,
exported as the quantized streaming model the satellite runs.

Run by the app (voice_call/wake_training.py) as a script -- not as part of the
package: it needs TensorFlow and microWakeWord, which the app itself never
imports -- with a job file the app wrote::

    python voice_call/wake_trainer.py job.json

The recipe (micro-wake-word's notebooks/ON_DEVICE_WARM_START.md): the MixedNet
of the 54-word base model, every layer initialised from its backbone (its
head is new) and trainable, 10,000 steps down a learning-rate ladder. Then the
head's kernel and bias are divided by ``temperature`` before conversion:
straight out of the converter, most positives pin at the top uint8 level and
no cutoff can be placed; sigmoid(z / T) ranks the same and spreads them out.

Progress goes to stdout as JSON lines, everything else (TensorFlow's chatter,
microWakeWord's log) to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ARCHITECTURE = [
    # The base backbone's (26,049 parameters): anything else does not load.
    "--pointwise_filters", "64,64,64,64",
    "--repeat_in_block", "1,1,1,1",
    "--mixconv_kernel_sizes", "[5],[7,11],[9,15],[23]",
    "--residual_connection", "0,0,0,0",
    "--first_conv_filters", "32",
    "--first_conv_kernel_size", "5",
    "--stride", "3",
]

_progress = None


def report(**progress: object) -> None:
    assert _progress is not None
    _progress.write(json.dumps(progress) + "\n")
    _progress.flush()


def load_config(path: Path, flags, mixednet, modes) -> dict:
    """microWakeWord's model_train_eval.load_config, without importing that
    module (it pulls in the evaluation stack)."""
    import yaml  # pylint: disable=import-outside-toplevel

    config = yaml.load(path.read_text(), yaml.Loader)
    config["summaries_dir"] = os.path.join(config["train_dir"], "logs/")
    config["stride"] = flags.stride
    config["window_step_ms"] = config.get("window_step_ms", 20)
    desired = int(16000 * config["clip_duration_ms"] / 1000)
    window = int(16000 * 30 / 1000)
    step = int(config["stride"] * 16000 * config["window_step_ms"] / 1000)
    final = 0 if desired < window else 1 + int((desired - window) / step)
    config["spectrogram_length_final_layer"] = final
    config["spectrogram_length"] = final + mixednet.spectrogram_slices_dropped(flags)
    config["flags"] = flags.__dict__
    config["training_input_shape"] = modes.get_input_data_shape(config, modes.Modes.TRAINING)
    return config


def write_mmaps(source: Path, target: Path, name: str) -> int:
    """The generated positives (an .npy of equal-length spectrograms) as the
    RaggedMmap microWakeWord reads."""
    import numpy as np  # pylint: disable=import-outside-toplevel
    from mmap_ninja.ragged import RaggedMmap  # pylint: disable=import-outside-toplevel

    spectrograms = np.load(source, mmap_mode="r")
    out = target / f"{name}_mmap"
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(spectrograms):
        RaggedMmap.from_generator(
            out_dir=str(out), sample_generator=(np.asarray(s) for s in spectrograms), batch_size=1000, verbose=False
        )
    return len(spectrograms)


def main(argv: list[str] | None = None) -> int:
    global _progress  # pylint: disable=global-statement
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job", type=Path)
    args = parser.parse_args(argv)
    job = json.loads(args.job.read_text())

    # Progress on our own copy of stdout; what microWakeWord prints there (a
    # line per step, ended by "\r") goes nowhere.
    _progress = os.fdopen(os.dup(1), "w")
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "1")

    work = Path(job["work"])
    started = time.monotonic()
    report(stage="preparing")
    positives = work / "positives"
    counts = {
        split: write_mmaps(Path(job["positives"][split]), positives / split, f"positives_{split}")
        for split in ("training", "validation")
    }
    if not counts["training"]:
        report(error="No positives were made")
        return 1

    # pylint: disable=import-outside-toplevel
    import numpy as np
    import tensorflow as tf
    import yaml
    from absl import logging
    from microwakeword import data as input_data
    from microwakeword import mixednet, train, utils
    from microwakeword.layers import modes

    logging.set_verbosity(logging.INFO)
    if job.get("seed") is not None:
        tf.keras.utils.set_random_seed(int(job["seed"]))
    threads = int(job.get("threads") or 0)
    if threads:
        tf.config.threading.set_intra_op_parallelism_threads(threads)

    flag_parser = argparse.ArgumentParser()
    mixednet.model_parameters(flag_parser)
    flags = flag_parser.parse_args(ARCHITECTURE)

    train_dir = work / "model"
    features = [
        {
            "features_dir": str(positives), "type": "mmap", "truth": True,
            "sampling_weight": job["positive"]["sampling_weight"], "penalty_weight": job["positive"]["penalty_weight"],
            "truncation_strategy": "truncate_start",
        },
        *(
            {
                "features_dir": negative["dir"], "type": "mmap", "truth": False,
                "sampling_weight": negative["sampling_weight"], "penalty_weight": negative["penalty_weight"],
                "truncation_strategy": "random",
            }
            for negative in job["negatives"]
        ),
    ]
    settings = {
        "batch_size": job["batch_size"],
        "clip_duration_ms": 1500,
        "window_step_ms": 10,
        # One evaluation, at the last step: without an ambient set the best
        # weights would freeze at the first, and the last are the ones kept.
        "eval_step_interval": 10_000_000,
        "training_steps": job["training_steps"],
        "learning_rates": job["learning_rates"],
        "positive_class_weight": [1], "negative_class_weight": [1],
        "time_mask_count": [0], "time_mask_max_size": [0], "freq_mask_count": [0], "freq_mask_max_size": [0],
        "maximization_metric": "average_viable_recall", "minimization_metric": None, "target_minimization": 0.9,
        "train_dir": str(train_dir),
        "features": features,
    }
    config_path = work / "training_parameters.yaml"
    config_path.write_text(yaml.dump(settings, default_flow_style=False))
    config = load_config(config_path, flags, mixednet, modes)
    if config["spectrogram_length"] != job["frames"]:
        report(error=f"The model looks at {config['spectrogram_length']} frames, the positives have {job['frames']}")
        return 1

    data = input_data.FeatureHandler(config)
    model = mixednet.model(flags, config["training_input_shape"], config["batch_size"])
    if job.get("backbone"):
        before = [w.numpy().copy() for w in model.weights]
        # The backbone has no head: that one tensor stays as initialised.
        model.load_weights(job["backbone"], skip_mismatch=True)
        missed = sum(1 for w, b in zip(model.weights, before) if (w.numpy() == b).all())
        loaded = len(before) - missed
        logging.info("initialised %d of %d tensors from %s", loaded, len(before), job["backbone"])
        if loaded < len(before) // 2:
            report(error=f"The backbone doesn't fit the model ({loaded} of {len(before)} tensors loaded)")
            return 1

    total = int(sum(job["training_steps"]))
    steps = 0
    reported = time.monotonic()
    train_on_batch = model.train_on_batch

    def counted(*a, **k):
        nonlocal steps, reported
        result = train_on_batch(*a, **k)
        steps += 1
        now = time.monotonic()
        if steps == total or now - reported >= 2.0:
            reported = now
            report(stage="training", done=steps, total=total)
        return result

    model.train_on_batch = counted
    train_dir.mkdir(parents=True)
    (train_dir / "logs").mkdir()
    report(stage="training", done=0, total=total)
    train.train(model, config, data)

    report(stage="converting")
    model = mixednet.model(flags, shape=config["training_input_shape"], batch_size=1)
    model.load_weights(str(train_dir / "last_weights.weights.h5"))
    temperature = float(job["temperature"])
    # By type, not name: a second model in a process names its head "dense_1".
    head = [layer for layer in model.layers if isinstance(layer, tf.keras.layers.Dense)][-1]
    kernel, bias = head.get_weights()
    head.set_weights([kernel / temperature, bias / temperature])
    utils.convert_model_saved(
        model, config, folder="stream_state_internal", mode=modes.Modes.STREAM_INTERNAL_STATE_INFERENCE
    )
    out_dir = train_dir / "tflite_stream_state_internal_quant"
    utils.convert_saved_model_to_tflite(
        config, audio_processor=data, path_to_model=str(train_dir / "stream_state_internal"),
        folder=str(out_dir), fname="stream_state_internal_quant.tflite", quantize=True,
    )
    tflite = out_dir / "stream_state_internal_quant.tflite"
    out = Path(job["out"])
    out.write_bytes(tflite.read_bytes())
    report(
        stage="done", tflite=str(out), seconds=round(time.monotonic() - started, 1), positives=counts,
        parameters=int(np.sum([np.prod(w.shape) for w in model.weights])),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
