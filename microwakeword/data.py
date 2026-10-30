# coding=utf-8
# Copyright 2024 Kevin Ahrendt.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Functions and classes for loading/augmenting spectrograms"""

import os
import random

import numpy as np

from absl import logging
from pathlib import Path
from mmap_ninja.ragged import RaggedMmap

# Imported lazily in `_build_clips_generator`. The audio stack pulls in
# audio_metadata, webrtcvad, pymicro_features and audiomentations, none of which a
# `type: mmap` feature set touches -- only the on-the-fly `clips_settings` path builds
# them. Keeping these at module scope made every mmap-only run, including a warm-start
# finetune on a Pi, depend on the whole generation stack.


def spec_augment(
    spectrogram: np.ndarray,
    time_mask_max_size: int = 0,
    time_mask_count: int = 0,
    freq_mask_max_size: int = 0,
    freq_mask_count: int = 0,
):
    """Applies SpecAugment to the input spectrogram.
    Based on SpecAugment: A Simple Data Augmentation Method for Automatic Speech Recognition by D. Park, W. Chan, Y. Zhang, C. Chiu, B. Zoph, E Cubuk, Q Le
    https://arxiv.org/pdf/1904.08779.pdf
    Implementation based on https://github.com/pyyush/SpecAugment/tree/master

    Args:
        spectrogram (numpy.ndarray): The input spectrogram.
        time_mask_max_size (int): The maximum size of time feature masks. Defaults to 0.
        time_mask_count (int): The total number of separate time masks. Defaults to 0.
        freq_mask_max_size (int): The maximum size of frequency feature masks. Defaults to 0.
        time_mask_count (int): The total number of separate feature masks. Defaults to 0.

    Returns:
        numpy.ndarray: The masked spectrogram.
    """

    time_frames = spectrogram.shape[0]
    freq_bins = spectrogram.shape[1]

    # Spectrograms yielded from a generator are read only
    augmented_spectrogram = np.copy(spectrogram)

    for i in range(time_mask_count):
        t = int(np.random.uniform(0, time_mask_max_size))
        t0 = random.randint(0, time_frames - t)
        augmented_spectrogram[t0 : t0 + t, :] = 0

    for i in range(freq_mask_count):
        f = int(np.random.uniform(0, freq_mask_max_size))
        f0 = random.randint(0, freq_bins - f)
        augmented_spectrogram[:, f0 : f0 + f] = 0

    return augmented_spectrogram


def fixed_length_spectrogram(
    spectrogram: np.ndarray,
    features_length: int,
    truncation_strategy: str = "random",
    right_cutoff: int = 0,
):
    """Returns a spectrogram with specified length. Pads with zeros at the start if too short. Removes feature windows following ``truncation_strategy`` if too long.

    Args:
        spectrogram (numpy.ndarray): The spectrogram to truncate or pad.
        features_length (int): The desired spectrogram length.
        truncation_strategy (str): How to truncate if ``spectrogram`` is longer than ``features_length`` One of:
            random: choose a random portion of the entire spectrogram - useful for long negative samples
            truncate_start: remove the start of the spectrogram
            truncate_end: remove the end of the spectrogram
            none: returns the entire spectrogram regardless of features_length


    Returns:
        numpy.ndarry: The fixed length spectrogram due to padding or truncation.
    """

    data_length = spectrogram.shape[0]
    features_offset = 0
    if data_length > features_length:
        if truncation_strategy == "random":
            features_offset = np.random.randint(0, data_length - features_length)
        elif truncation_strategy == "none":
            # return the entire spectrogram
            features_length = data_length
        elif truncation_strategy == "truncate_start":
            features_offset = data_length - features_length
        elif truncation_strategy == "truncate_end":
            features_offset = 0
        elif truncation_strategy == "fixed_right_cutoff":
            features_offset = data_length - features_length - right_cutoff
    else:
        pad_slices = features_length - data_length

        spectrogram = np.pad(
            spectrogram, ((pad_slices, 0), (0, 0)), constant_values=(0, 0)
        )
        features_offset = 0

    return spectrogram[features_offset : (features_offset + features_length)]


class MmapFeatureGenerator(object):
    """A class that handles loading spectrograms from Ragged MMaps for training or testing.

    Args:
        path (str): Input directory to the Ragged MMaps. The Ragged MMap folders should be included in the following file structure:
            training/ (spectrograms to use for training the model)
            validation/ (spectrograms used to validate the model while training)
            testing/ (spectrograms used to test the model after training)
            validation_ambient/ (spectrograms of long duration background audio clips that are split and validated while training)
            testing_ambient/ (spectrograms of long duration background audio clips to test the model after training)
        label (bool): The class each spectrogram represents; i.e., wakeword or not.
        sampling_weight (float): The sampling weight for how frequently a spectrogram from this dataset is chosen.
        penalty_weight (float): The penalizing weight for incorrect predictions for each spectrogram.
        truncation_strategy (str): How to truncate if ``spectrogram`` is too long.
        stride (int): The stride in the model's first layer.
        step (float): The window step duration (in seconds).
        fixed_right_cutoffs (list[int]): List of spectogram slices to cutoff on the right if the truncation strategy is "fixed_right_cutoff". In training mode, its randomly chosen from the list. Otherwise, it yields spectrograms with all cutoffs in the list.
        max_clips (int | None): Cap on how many spectrograms to take from the training split, sampled evenly across the set's mmaps. Lets a config ask how little of a negative pool a run actually needs, which is what decides whether training fits a device that cannot hold tens of gigabytes.
    """

    def __init__(
        self,
        path: str,
        label: bool,
        sampling_weight: float,
        penalty_weight: float,
        truncation_strategy: str,
        stride: int,
        step: float,
        fixed_right_cutoffs: list[int] = [0],
        class_id: int | None = None,
        max_clips: int | None = None,
    ):
        self.label = float(label)
        # Integer class id for many-way (base) training. Defaults to the binary label
        # (0 = negative / "none", 1 = positive) so binary training is unaffected.
        self.class_id = int(class_id) if class_id is not None else int(self.label)
        self.sampling_weight = sampling_weight
        self.penalty_weight = penalty_weight
        self.truncation_strategy = truncation_strategy
        self.fixed_right_cutoffs = fixed_right_cutoffs

        self.stride = stride
        self.step = step
        self.max_clips = max_clips

        self.stats = {}
        self.feature_sets = {}
        # feature_index -> np.ndarray of per-sample soft targets, or None
        self.soft_labels = {}

        self.feature_sets["testing"] = []
        self.feature_sets["training"] = []
        self.feature_sets["validation"] = []
        self.feature_sets["validation_ambient"] = []
        self.feature_sets["testing_ambient"] = []

        self.loaded_features = []

        dirs = [
            "testing",
            "training",
            "validation",
            "testing_ambient",
            "validation_ambient",
        ]

        for set_index in dirs:
            duration = 0.0
            count = 0

            search_path_directory = os.path.join(path, set_index)
            search_path = [
                str(i)
                for i in Path(os.path.abspath(search_path_directory)).glob("**/*_mmap/")
            ]

            for mmap_path in search_path:
                imported_features = RaggedMmap(mmap_path)

                self.loaded_features.append(imported_features)
                feature_index = len(self.loaded_features) - 1

                # Optional per-sample soft targets for knowledge distillation, written next to the
                # mmap as soft_labels.npy by notebooks/make_distill_features.py: one float in
                # [0,1] per spectrogram, the teacher's score for the exact augmented audio that
                # spectrogram came from. Where it is absent, every sample keeps the feature set's
                # constant `truth`, so existing configs behave identically.
                # Sibling of the mmap dir, not inside it (RaggedMmap owns that dir) and not
                # shared across the split dir, so a features_dir may hold several sharded
                # mmaps each with its own targets.
                soft_path = mmap_path.rstrip("/") + "_soft_labels.npy"
                soft = None
                if os.path.exists(soft_path):
                    soft = np.load(soft_path)
                    if len(soft) != len(imported_features):
                        raise ValueError(
                            f"{soft_path}: {len(soft)} labels for "
                            f"{len(imported_features)} spectrograms"
                        )
                self.soft_labels[feature_index] = soft

                for i in range(0, len(imported_features)):
                    self.feature_sets[set_index].append(
                        {
                            "loaded_feature_index": feature_index,
                            "subindex": i,
                        }
                    )

                    duration += step * imported_features[i].shape[0]
                    count += 1

            random.shuffle(self.feature_sets[set_index])

            # Cap only the training split: the validation and ambient splits define the
            # metric, and trimming them would change what the number means rather than
            # how much data the run needs. Applied after the shuffle so the survivors are
            # spread across every mmap instead of being the first shard.
            if self.max_clips is not None and set_index == "training":
                kept = self.feature_sets[set_index][: self.max_clips]
                if len(kept) < count:
                    duration *= len(kept) / count
                    count = len(kept)
                self.feature_sets[set_index] = kept

            self.stats[set_index] = {
                "spectrogram_count": count,
                "total_duration": duration,
            }

    def get_mode_duration(self, mode: str):
        """Retrieves the total duration of the spectrograms in the mode set.

        Args:
            mode (str): Specifies the set. One of "training", "validation", "testing", "validation_ambient", "testing_ambient".

        Returns:
            float: The duration in hours.
        """
        return self.stats[mode]["total_duration"]

    def get_mode_size(self, mode):
        """Retrieves the total count of the spectrograms in the mode set.

        Args:
            mode (str): Specifies the set. One of "training", "validation", "testing", "validation_ambient", "testing_ambient".

        Returns:
            int: The spectrogram count.
        """
        return self.stats[mode]["spectrogram_count"]

    def get_random_spectrogram(
        self, mode: str, features_length: int, truncation_strategy: str,
        return_label: bool = False,
    ):
        """Retrieves a random spectrogram from the specified mode with specified length after truncation.

        Args:
            mode (str): Specifies the set. One of "training", "validation", "testing", "validation_ambient", "testing_ambient".
            features_length (int): The length of the spectrogram in feature windows.
            truncation_strategy (str): How to truncate if ``spectrogram`` is too long.

        Returns:
            numpy.ndarray: A random spectrogram of specified length after truncation.
        """
        right_cutoff = 0
        if truncation_strategy == "default":
            truncation_strategy = self.truncation_strategy

        if truncation_strategy == "fixed_right_cutoff":
            right_cutoff = random.choice(self.fixed_right_cutoffs)

        feature = random.choice(self.feature_sets[mode])
        spectrogram = self.loaded_features[feature["loaded_feature_index"]][
            feature["subindex"]
        ]

        soft = self.soft_labels.get(feature["loaded_feature_index"])
        drawn_label = (
            float(soft[feature["subindex"]]) if soft is not None else self.label
        )

        spectrogram = fixed_length_spectrogram(
            spectrogram,
            features_length,
            truncation_strategy,
            right_cutoff,
        )

        # Spectrograms with type np.uint16 haven't been scaled
        if np.issubdtype(spectrogram.dtype, np.uint16):
            spectrogram = spectrogram.astype(np.float32) * 0.0390625

        if return_label:
            return spectrogram, drawn_label
        return spectrogram

    def get_feature_generator(
        self,
        mode,
        features_length,
        truncation_strategy="default",
    ):
        """A Python generator that yields spectrograms from the specified mode of specified length after truncation.

        Args:
            mode (str): Specifies the set. One of "training", "validation", "testing", "validation_ambient", "testing_ambient".
            features_length (int): The length of the spectrogram in feature windows.
            truncation_strategy (str): How to truncate if ``spectrogram`` is too long.

        Yields:
            numpy.ndarray: A random spectrogram of specified length after truncation.
        """
        if truncation_strategy == "default":
            truncation_strategy = self.truncation_strategy

        for feature in self.feature_sets[mode]:
            spectrogram = self.loaded_features[feature["loaded_feature_index"]][
                feature["subindex"]
            ]

            # Spectrograms with type np.uint16 haven't been scaled
            if np.issubdtype(spectrogram.dtype, np.uint16):
                spectrogram = spectrogram.astype(np.float32) * 0.0390625

            if truncation_strategy == "split":
                for feature_start_index in range(
                    0,
                    spectrogram.shape[0] - features_length,
                    int(1000 * self.step * self.stride),
                ):  # 10*2 features corresponds to 200 ms
                    split_spectrogram = spectrogram[
                        feature_start_index : feature_start_index + features_length
                    ]

                    yield split_spectrogram
            else:
                for cutoff in self.fixed_right_cutoffs:
                    fixed_spectrogram = fixed_length_spectrogram(
                        spectrogram,
                        features_length,
                        truncation_strategy,
                        cutoff,
                    )

                    yield fixed_spectrogram


class ClipsHandlerWrapperGenerator(object):
    """A class that handles loading spectrograms from audio files on the disk. Spectrograms are generated on demand, so any configured augmentations are applied freshly every time a clip is used.

    A feature set declares which modes it serves via ``modes``. Sets serving more than one mode must configure ``random_split_seed`` so that ``Clips`` partitions the directory; each mode then draws from its own disjoint split. Training draws randomly with replacement, while validation and testing iterate their split once, in order.

    Args:
        spectrogram_generation (SpectrogramGeneration): Object that handles generating spectrograms from audio files.
        label (bool): The class each spectrogram represents; i.e., wakeword or not.
        sampling_weight (float): The sampling weight for how frequently a spectrogram from this dataset is chosen.
        penalty_weight (float): The penalizing weight for incorrect predictions for each spectrogram.
        truncation_strategy (str): How to truncate if ``spectrogram`` is too long.
        modes (tuple[str, ...], optional): Which sets this feature set provides spectrograms for. Defaults to ``("training",)``.
    """

    # microWakeWord's mode names mapped to the split names Clips creates. The
    # ambient modes are absent deliberately: they require the duration
    # accounting that get_mode_duration cannot provide for on-demand clips.
    MODE_TO_SPLIT = {
        "training": "train",
        "validation": "validation",
        "testing": "test",
    }

    def __init__(
        self,
        spectrogram_generation: "SpectrogramGeneration",
        label: bool,
        sampling_weight: float,
        penalty_weight: float,
        truncation_strategy: str,
        class_id: int | None = None,
        modes: tuple[str, ...] = ("training",),
    ):
        self.spectrogram_generation = spectrogram_generation
        self.label = label
        self.class_id = int(class_id) if class_id is not None else int(bool(label))
        self.sampling_weight = sampling_weight
        self.penalty_weight = penalty_weight
        self.truncation_strategy = truncation_strategy
        self.modes = tuple(modes)

        if unsupported := sorted(set(self.modes) - set(self.MODE_TO_SPLIT)):
            raise ValueError(
                f"A clips feature set cannot serve mode(s) {unsupported}; choose "
                f"from {sorted(self.MODE_TO_SPLIT)}."
            )

        # Clips only partitions the directory when random_split_seed is set.
        # Without splits every mode would draw from the full clip list, which
        # silently reuses training clips for validation and testing.
        self.splits = getattr(spectrogram_generation.clips, "split_clips", None)

        if self.splits is None and len(self.modes) > 1:
            raise ValueError(
                "A clips feature set serving more than one mode must set "
                "`random_split_seed` in its clips_settings, otherwise the same "
                f"clips would be used for all of {list(self.modes)}."
            )

        self.augmented_generator = self.spectrogram_generation.spectrogram_generator(
            random=True, split=self.split_for("training")
        )

    def split_for(self, mode: str):
        """Returns the Clips split name backing ``mode``, or None when the clips are unpartitioned."""
        if self.splits is None:
            return None
        return self.MODE_TO_SPLIT[mode]

    def clip_list_for(self, mode: str):
        """Returns the list of clips backing ``mode``."""
        if (split := self.split_for(mode)) is None:
            return self.spectrogram_generation.clips.clips
        return self.splits[split]

    def get_mode_duration(self, mode):
        """Function to maintain compatability with the MmapFeatureGenerator class."""
        return 0.0

    def get_mode_size(self, mode):
        """Returns the count of spectrograms this feature set provides for ``mode``.

        The count is exact unless ``split_spectrogram_duration_s`` is configured, in which case the number of spectrograms per clip depends on each clip's duration and this is a lower bound.
        """
        if mode not in self.modes:
            return 0

        count = len(self.clip_list_for(mode))

        # Sliding yields slide_frames spectrograms from every clip.
        if self.spectrogram_generation.slide_frames is not None:
            count *= self.spectrogram_generation.slide_frames

        return count

    def get_random_spectrogram(
        self, mode, features_length, truncation_strategy, return_label: bool = False
    ):
        """Retrieves a random spectrogram from the specified mode with specified length after truncation.

        Args:
            mode (str): Specifies the set, but is ignored for this class. It is assumed the spectrograms will be for training.
            features_length (int): The length of the spectrogram in feature windows.
            truncation_strategy (str): How to truncate if ``spectrogram`` is too long.
            return_label (bool): Also return the sample's label, so callers can share one code
                path with MmapFeatureGenerator. Clips providers are augmented on the fly and have
                no precomputed soft targets, so this is always the feature set's constant label.

        Returns:
            numpy.ndarray: A random spectrogram of specified length after truncation.
        """

        if truncation_strategy == "default":
            truncation_strategy = self.truncation_strategy

        spectrogram = next(self.augmented_generator)

        spectrogram = fixed_length_spectrogram(
            spectrogram,
            features_length,
            truncation_strategy,
            right_cutoff=0,
        )

        # Spectrograms with type np.uint16 haven't been scaled
        if np.issubdtype(spectrogram.dtype, np.uint16):
            spectrogram = spectrogram.astype(np.float32) * 0.0390625

        if return_label:
            return spectrogram, float(self.label)
        return spectrogram

    def get_feature_generator(
        self,
        mode,
        features_length,
        truncation_strategy="default",
    ):
        """A Python generator that yields every spectrogram this feature set provides for ``mode``.

        Unlike ``get_random_spectrogram``, the clips are traversed once in order, so a validation or testing set is identical from one evaluation to the next as long as no augmentations are configured.

        Args:
            mode (str): which set to generate spectrograms for. One of `training`, `validation`, or `testing`.
            features_length (int): The length of the spectrogram in feature windows.
            truncation_strategy (str): How to truncate if a ``spectrogram`` is too long.

        Yields:
            numpy.ndarray: A spectrogram of specified length after truncation.
        """
        if mode not in self.modes:
            return

        if truncation_strategy == "default":
            truncation_strategy = self.truncation_strategy

        generator = self.spectrogram_generation.spectrogram_generator(
            random=False, split=self.split_for(mode), repeat=1
        )

        for spectrogram in generator:
            spectrogram = fixed_length_spectrogram(
                spectrogram,
                features_length,
                truncation_strategy,
                right_cutoff=0,
            )

            # Spectrograms with type np.uint16 haven't been scaled
            if np.issubdtype(spectrogram.dtype, np.uint16):
                spectrogram = spectrogram.astype(np.float32) * 0.0390625

            yield spectrogram


def build_feature_provider(feature_set: dict, config: dict):
    """Constructs one feature provider from its config entry.

    Split out of FeatureHandler.__init__ so that the parallel training workers can rebuild the
    clips providers in their own processes. A live provider cannot be shipped to a worker: a
    ClipsHandlerWrapperGenerator holds an open Python generator, which is not picklable. Everything
    it needs, though, is in the plain config dict.
    """
    if feature_set["type"] == "mmap":
        return MmapFeatureGenerator(
            feature_set["features_dir"],
            feature_set["truth"],
            feature_set["sampling_weight"],
            feature_set["penalty_weight"],
            feature_set["truncation_strategy"],
            stride=config["stride"],
            step=config["window_step_ms"] / 1000.0,
            fixed_right_cutoffs=feature_set.get("fixed_right_cutoffs", [0]),
            class_id=feature_set.get("class_id"),
            max_clips=feature_set.get("max_clips"),
        )

    if feature_set["type"] == "clips":
        from microwakeword.audio.augmentation import Augmentation
        from microwakeword.audio.clips import Clips
        from microwakeword.audio.spectrograms import SpectrogramGeneration

        clips_handler = Clips(**feature_set["clips_settings"])

        # Augmentation is optional. Omitting it yields the clips as they
        # are on disk, which is usually what a validation or testing set
        # wants so that its spectrograms never change between runs.
        augmentation_settings = feature_set.get("augmentation_settings")
        augmentation_applier = (
            Augmentation(**augmentation_settings)
            if augmentation_settings is not None
            else None
        )

        # SpectrogramGeneration defaults step_ms to 20, which silently
        # halves the feature resolution when the rest of the config is
        # configured for 10 ms windows.
        spectrogram_generation_settings = dict(
            feature_set.get("spectrogram_generation_settings", {})
        )
        spectrogram_generation_settings.setdefault("step_ms", config["window_step_ms"])

        spectrogram_generator = SpectrogramGeneration(
            clips_handler,
            augmentation_applier,
            **spectrogram_generation_settings,
        )
        return ClipsHandlerWrapperGenerator(
            spectrogram_generator,
            feature_set["truth"],
            feature_set["sampling_weight"],
            feature_set["penalty_weight"],
            feature_set["truncation_strategy"],
            class_id=feature_set.get("class_id"),
            modes=feature_set.get("modes", ("training",)),
        )

    return None


def _clips_producer(config: dict, indices: list, seed: int, chunk_size: int, queue):
    """Worker entry point: forever push chunks of training samples onto ``queue``.

    Runs in its own process under the "spawn" start method, so this must be importable at module
    level and every argument picklable. Spawn rather than fork because the parent has TensorFlow
    loaded across ~85 threads, and forking a multi-threaded process that holds TF state is a
    well-known deadlock source.

    Each worker draws providers with the same relative sampling weights the main process would use
    among clips providers, so the marginal distribution of (label, penalty_weight) is unchanged --
    the main process can therefore take whichever sample comes next off the queue without caring
    which provider produced it.

    spec_augment deliberately stays in the parent: its policy changes between training stages, and
    keeping it there avoids having to push per-stage policy updates out to the workers.
    """
    # Nothing here touches the GPU, and TensorFlow gets pulled in transitively by the audio
    # frontend module, so hide the devices before that import can grab memory.
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

    rng = random.Random(seed)
    np.random.seed(seed % (2**32))

    providers = [
        build_feature_provider(config["features"][index], config) for index in indices
    ]
    weights = [provider.sampling_weight for provider in providers]

    truncation = "default"
    features_length = config["spectrogram_length"]

    while True:
        chunk = []
        for provider in rng.choices(providers, weights, k=chunk_size):
            spectrogram, drawn_label = provider.get_random_spectrogram(
                "training", features_length, truncation, return_label=True
            )
            chunk.append(
                (
                    np.asarray(spectrogram, dtype=np.float32),
                    float(drawn_label),
                    int(provider.class_id),
                    float(provider.penalty_weight),
                )
            )
        # Blocks once the queue is full, which is what bounds memory and stops the workers
        # from racing ahead of training.
        queue.put(chunk)


class ClipsProducerPool:
    """A pool of processes that generate clips-backed training samples ahead of the training loop.

    The augmentation pipeline is pure Python and single threaded -- roughly 5.7 ms per sample
    (load 0.7, augment 1.9, featurize 3.1), so a 128-sample batch drawn mostly from clips
    providers costs a few hundred ms while the GPU sits idle. The work is independent per sample,
    so it parallelizes cleanly; this just moves it off the training thread.
    """

    def __init__(self, config: dict, indices: list, num_workers: int, chunk_size: int = 16):
        import multiprocessing

        self.chunk_size = chunk_size
        self.indices = indices
        # Spawn, not fork -- see _clips_producer.
        context = multiprocessing.get_context("spawn")
        # Bounded so the workers cannot run away with memory; a few batches of lead is plenty.
        self.queue = context.Queue(maxsize=max(4, 512 // chunk_size))
        self._buffer = []
        self.workers = []
        # Workers take ~20s to come up under spawn (fresh interpreter, TensorFlow import, one
        # Augmentation per clips feature set). Until the first sample arrives, a timeout in get()
        # means "still starting", not "stuck".
        self._started = False

        base_seed = int(config.get("training_worker_seed", 0))
        for worker_id in range(num_workers):
            worker = context.Process(
                target=_clips_producer,
                args=(config, indices, base_seed + worker_id + 1, chunk_size, self.queue),
                daemon=True,
            )
            worker.start()
            self.workers.append(worker)

        logging.info(
            "started %d clips producer worker(s) over %d clips feature set(s)",
            num_workers,
            len(indices),
        )

        # No liveness check here on purpose: a worker is is_alive() the instant start() returns,
        # even one that is about to die, so any check at this point is a race. get() detects a
        # dead pool reliably instead.

    def get(self):
        """Returns one (spectrogram, label, class_id, penalty_weight) tuple.

        Waits with a timeout rather than blocking outright: if every worker has died -- a bad
        config, an unreadable clip directory, a missing dependency in the spawned interpreter --
        an unguarded ``queue.get()`` would hang the training run forever with no diagnostic.
        """
        import queue as queue_module

        while not self._buffer:
            try:
                self._buffer = list(self.queue.get(timeout=10))
                self._started = True
            except queue_module.Empty:
                if not any(worker.is_alive() for worker in self.workers):
                    codes = [worker.exitcode for worker in self.workers]
                    raise RuntimeError(
                        "every clips producer worker has exited (exit codes "
                        f"{codes}); the cause is in the traceback above this line. If it "
                        "mentions bootstrapping, the entry point needs an "
                        '`if __name__ == "__main__":` guard, because the spawn start method '
                        "re-imports __main__ in each child. Set training_workers: 0 to fall "
                        "back to serial generation."
                    )
                alive = sum(worker.is_alive() for worker in self.workers)
                if self._started:
                    logging.warning(
                        "waited 10s for a training sample; %d/%d worker(s) still alive",
                        alive,
                        len(self.workers),
                    )
                else:
                    logging.info(
                        "waiting for %d clips producer worker(s) to finish starting", alive
                    )
        return self._buffer.pop()

    def close(self):
        for worker in self.workers:
            worker.terminate()
        for worker in self.workers:
            worker.join(timeout=5)


class FeatureHandler(object):
    """Class that handles loading spectrogram features and providing them to the training and testing functions.

    Args:
      config: dictionary containing microWakeWord training configuration
    """

    def __init__(
        self,
        config: dict,
    ):
        self.feature_providers = []
        self.producer_pool = None

        logging.info("Loading and analyzing data sets.")

        unusable_sets = []

        for feature_set in config["features"]:
            provider = build_feature_provider(feature_set, config)
            if provider is None:
                # The old check read feature_providers[-1] here, which silently re-checked the
                # previous set instead of this one.
                unusable_sets.append(
                    (feature_set, {}, "no feature provider could be built for this set")
                )
                continue
            self.feature_providers.append(provider)

            set_modes = [
                "training",
                "validation",
                "testing",
                "validation_ambient",
                "testing_ambient",
            ]
            mode_sizes = {mode: provider.get_mode_size(mode) for mode in set_modes}

            # A configured set that yields nothing usable is always a mistake, and this used to
            # only warn. A stale `features_dir` then left both training and the int8 calibration
            # draw in utils.convert_model_saved running on whichever sets did resolve, which
            # produces a model rather than an error -- one quantized against, in the case that
            # motivated this check, ambient noise with no positives in it at all.
            if sum(mode_sizes.values()) == 0:
                unusable_sets.append(
                    (feature_set, mode_sizes, "no spectrograms in any split")
                )
            elif feature_set["sampling_weight"] > 0 and mode_sizes["training"] == 0:
                # get_data("training") skips providers with an empty training split, so such a
                # set is silently dropped from every batch no matter how it is weighted.
                unusable_sets.append(
                    (
                        feature_set,
                        mode_sizes,
                        "sampling_weight is %s but the training split is empty, so this set "
                        "can never be sampled" % feature_set["sampling_weight"],
                    )
                )

        if unusable_sets:
            details = []
            for feature_set, mode_sizes, reason in unusable_sets:
                location = feature_set.get("features_dir") or feature_set.get(
                    "clips_settings", {}
                ).get("input_directory", "<no location in config>")
                counts = (
                    ", ".join(f"{m}={n}" for m, n in mode_sizes.items()) or "nothing"
                )
                details.append(f"  {location}\n    {reason}\n    found: {counts}")
            raise ValueError(
                "%d configured feature set(s) contribute no usable spectrograms:\n%s\n\n"
                "An mmap set is read from <features_dir>/<split>/**/*_mmap. Check for a stale "
                "path, or a split directory renamed out from under the config."
                % (len(unusable_sets), "\n".join(details))
            )

        # Optional prefetch pool for the training path only. Validation and testing keep running
        # serially and in order, because their spectrograms must be identical between evaluations.
        num_workers = int(config.get("training_workers", 0) or 0)
        clips_indices = [
            index
            for index, feature_set in enumerate(config["features"])
            if feature_set["type"] == "clips"
            and "training" in feature_set.get("modes", ("training",))
            and feature_set["sampling_weight"] > 0
        ]
        if num_workers > 0 and clips_indices:
            self.producer_pool = ClipsProducerPool(
                config,
                clips_indices,
                num_workers,
                chunk_size=int(config.get("training_worker_chunk", 16)),
            )
        elif num_workers > 0:
            logging.info("training_workers set but no clips feature sets train; staying serial")

    def close(self):
        """Stops the prefetch workers, if any. Safe to call more than once."""
        if self.producer_pool is not None:
            self.producer_pool.close()
            self.producer_pool = None

    def get_mode_duration(self, mode: str):
        """Returns the durations of all spectrogram features in the given mode.

        Args:
            mode (str): which training set to compute duration over. One of `training`, `testing`, `testing_ambient`, `validation`, or `validation_ambient`

        Returns:
            duration, in seconds, of all spectrograms in this mode
        """

        sample_duration = 0
        for provider in self.feature_providers:
            sample_duration += provider.get_mode_duration(mode)
        return sample_duration

    def get_mode_size(self, mode: str):
        """Returns the count of all spectrogram features in the given mode.

        Args:
            mode (str): which training set to count the spectrograms. One of `training`, `testing`, `testing_ambient`, `validation`, or `validation_ambient`

        Returns:
            count of spectrograms in given mode
        """
        sample_count = 0
        for provider in self.feature_providers:
            sample_count += provider.get_mode_size(mode)
        return sample_count

    def get_data(
        self,
        mode: str,
        batch_size: int,
        features_length: int,
        truncation_strategy: str = "default",
        augmentation_policy: dict = {
            "freq_mix_prob": 0.0,
            "time_mask_max_size": 0,
            "time_mask_count": 0,
            "freq_mask_max_size": 0,
            "freq_mask_count": 0,
        },
        multiclass: bool = False,
    ):
        """Gets spectrograms from the appropriate mode. Ensures spectrograms are the approriate length and optionally applies augmentation.

        Args:
            mode (str): which training set to count the spectrograms. One of `training`, `testing`, `testing_ambient`, `validation`, or `validation_ambient`
            batch_size (int): number of spectrograms in the sample for training mode
            features_length (int): the length of the spectrograms
            truncation_strategy (str): how to truncate spectrograms longer than `features_length`
            augmentation_policy (dict): dictionary that specifies augmentation settings. It has the following keys:
                freq_mix_prob: probability that FreqMix is applied
                time_mask_max_size: maximum size of time masks for SpecAugment
                time_mask_count: the total number of separate time masks applied for SpecAugment
                freq_mask_max_size: maximum size of frequency feature masks for SpecAugment
                freq_mask_count: the total number of separate feature masks applied for SpecAugment

        Returns:
            data: spectrograms in a NumPy array (or as a list if in mode is `*_ambient`)
            labels: ground truth for the spectrograms; i.e., whether a positive sample or negative sample
            weights: penalizing weight for incorrect predictions for each spectrogram
        """

        if mode == "training":
            sample_count = batch_size
        elif (mode == "validation") or (mode == "testing"):
            sample_count = self.get_mode_size(mode)

        data = []
        labels = []
        weights = []

        if mode == "training":
            random_feature_providers = random.choices(
                [
                    provider
                    for provider in self.feature_providers
                    if provider.get_mode_size("training")
                ],
                [
                    provider.sampling_weight
                    for provider in self.feature_providers
                    if provider.get_mode_size("training")
                ],
                k=sample_count,
            )

            for provider in random_feature_providers:
                if self.producer_pool is not None and isinstance(
                    provider, ClipsHandlerWrapperGenerator
                ):
                    # The workers already drew a clips provider using the same relative sampling
                    # weights, so taking whichever sample is next off the queue leaves the batch
                    # distribution unchanged -- only this draw's identity is discarded, not its
                    # weight. mmap providers stay local: they are array indexing, and shipping
                    # them through a queue would cost more than producing them.
                    spectrogram, label, class_id, penalty_weight = (
                        self.producer_pool.get()
                    )
                else:
                    spectrogram, label = provider.get_random_spectrogram(
                        "training", features_length, truncation_strategy,
                        return_label=True,
                    )
                    label = float(label)
                    class_id = int(provider.class_id)
                    penalty_weight = float(provider.penalty_weight)

                spectrogram = spec_augment(
                    spectrogram,
                    augmentation_policy["time_mask_max_size"],
                    augmentation_policy["time_mask_count"],
                    augmentation_policy["freq_mask_max_size"],
                    augmentation_policy["freq_mask_count"],
                )

                data.append(spectrogram)
                labels.append(class_id if multiclass else label)
                weights.append(penalty_weight)
        else:
            for provider in self.feature_providers:
                generator = provider.get_feature_generator(
                    mode, features_length, truncation_strategy
                )

                for spectrogram in generator:
                    data.append(spectrogram)
                    labels.append(
                        int(provider.class_id) if multiclass else provider.label
                    )
                    weights.append(provider.penalty_weight)

        if truncation_strategy != "none":
            # Spectrograms are all the same length, convert to numpy array
            data = np.array(data)
        labels = np.array(labels)
        weights = np.array(weights)

        if truncation_strategy == "none":
            # Spectrograms may be of different length
            return data, np.array(labels), np.array(weights)

        indices = np.arange(labels.shape[0])

        if mode == "testing" or "validation":
            # Randomize the order of the data, weights, and labels
            np.random.shuffle(indices)

        return data[indices], labels[indices], weights[indices]
