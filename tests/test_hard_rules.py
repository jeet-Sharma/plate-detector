"""The project's hard rules, checked statically against the runtime package."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parent.parent / "anpr"
MAIN = PACKAGE.parent / "main.py"

NEVER_AT_RUNTIME = {
    "torch", "tensorflow", "keras", "ultralytics", "fast_plate_ocr", "open_image_models",
    "requests", "urllib", "http", "socket", "httpx", "aiohttp",
}
PURE_MODULES = {"plate_format.py", "tracker.py", "types.py"}
HEAVY = {"cv2", "onnxruntime", "av"}


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def runtime_files() -> list[Path]:
    return [*sorted(PACKAGE.glob("*.py")), MAIN]


@pytest.mark.parametrize("path", runtime_files(), ids=lambda p: p.name)
def test_no_deep_learning_framework_or_network_imports_at_runtime(path):
    assert not imported_roots(path) & NEVER_AT_RUNTIME


@pytest.mark.parametrize("name", sorted(PURE_MODULES))
def test_pure_modules_do_not_import_opencv_or_onnxruntime(name):
    assert not imported_roots(PACKAGE / name) & HEAVY


def test_package_init_stays_light_so_env_vars_apply_before_numpy_loads():
    assert not imported_roots(PACKAGE / "__init__.py") - {"__future__"}
    assert imported_roots(PACKAGE / "runtime.py") <= {"__future__", "os"}


UI_PACKAGE = PACKAGE.parent / "ui"
UI_NEVER = {"torch", "tensorflow", "keras", "ultralytics", "fast_plate_ocr", "open_image_models",
            "requests", "httpx", "aiohttp", "socket"}


@pytest.mark.parametrize("path", sorted(UI_PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_ui_uses_the_standard_library_and_no_network_clients(path):
    assert not imported_roots(path) & UI_NEVER


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_the_recognition_package_never_imports_the_ui(path):
    assert "ui" not in imported_roots(path)


def test_the_ui_page_makes_no_requests_to_other_sites():
    static = UI_PACKAGE / "static"
    for name in ("index.html", "app.js", "app.css"):
        text = (static / name).read_text(encoding="utf-8")
        for marker in ("http://", "https://"):
            for line in text.splitlines():
                # the SVG namespace in the inline favicon is not a request
                if marker in line and "w3.org/2000/svg" not in line:
                    raise AssertionError(f"{name} refers to an external address: {line.strip()}")
