import pytest

from anpr.config import PRESETS, build_config
from anpr.runtime import physical_cores

pytestmark = pytest.mark.unit


def test_presets_exist():
    assert set(PRESETS) == {"low-mem", "balanced", "fast"}


def test_override_beats_preset():
    cfg = build_config("a.mp4", preset="balanced", sample_fps=2.0)
    assert cfg.sample_fps == 2.0
    assert cfg.det_mode == "tiled"


def test_none_override_is_ignored():
    cfg = build_config("a.mp4", sample_fps=None)
    assert cfg.sample_fps == PRESETS["balanced"]["sample_fps"]


def test_threads_capped_at_physical_cores():
    cfg = build_config("a.mp4", threads=999, decode_threads=999)
    assert cfg.threads == physical_cores()
    assert cfg.decode_threads == physical_cores()


def test_unknown_preset_and_setting_rejected():
    with pytest.raises(ValueError):
        build_config("a.mp4", preset="turbo")
    with pytest.raises(ValueError):
        build_config("a.mp4", nonsense=1)
