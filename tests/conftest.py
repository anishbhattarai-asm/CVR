import json
import os

import pytest

SAMPLE = '''\
import math


def area(radius: float) -> float:
    """Area of a circle."""
    return math.pi * radius ** 2  # pi r squared


if __name__ == "__main__":
    print(area(2))
'''


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / "circle.py"
    path.write_text(SAMPLE, encoding="utf-8")
    return str(path)


@pytest.fixture
def small():
    """Renderer options that keep test renders quick."""
    return dict(resolution=(320, 180), fps=10, font_size=12)


def write_json(path, data, header=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + json.dumps(data, indent=2))
    return path
