"""PNG export for review plots."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from matplotlib.figure import Figure


def export_png_plots(directory: Path, figures: Mapping[str, Figure]) -> list[Path]:
    """Export named Matplotlib figures as review-quality PNG files."""

    destination = directory.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, figure in figures.items():
        path = destination / f"{name}.png"
        figure.savefig(path, dpi=200)
        paths.append(path)
    return paths
