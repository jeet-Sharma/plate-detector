"""Plate OCR: fast-plate-ocr CCT-S (ONNX), loaded directly with ONNX Runtime.

Model contract (checked against the fast-plate-ocr source and the model's I/O):
  input  uint8 (N, 64, 128, 3) RGB, normalisation happens inside the model
  output ``plate`` float32 (N, 10, 37): per-slot probabilities over ``0-9A-Z_``
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from anpr.ort_utils import make_session
from anpr.types import PlateAlphabet


class PlateOCR:
    def __init__(
        self, model_path: Path, config_path: Path, threads: int, arena: bool = True
    ) -> None:
        self.session = make_session(model_path, threads, arena)
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.alphabet = PlateAlphabet(config["alphabet"], config.get("pad_char", "_"))
        self.slots = int(config["max_plate_slots"])
        self.height = int(config["img_height"])
        self.width = int(config["img_width"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        self._plate_index = self.output_names.index("plate")

    def read(self, images: list[np.ndarray]) -> list[np.ndarray]:
        """Slot probabilities, shape (slots, alphabet), one array per input image.

        Each image is RGB uint8 of shape (height, width, 3).
        """
        if not images:
            return []
        batch = np.stack(images).astype(np.uint8, copy=False)
        outputs = self.session.run(self.output_names, {self.input_name: batch})
        vocab = len(self.alphabet.chars)
        probs = outputs[self._plate_index].reshape(len(images), self.slots, vocab)
        return [probs[i] for i in range(len(images))]
