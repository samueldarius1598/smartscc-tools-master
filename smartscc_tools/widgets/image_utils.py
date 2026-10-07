"""Helpers for loading Tk images with quality-preserving resize."""

from __future__ import annotations

import math
from pathlib import Path
import tkinter as tk

try:
    from PIL import Image, ImageTk
except Exception:  # pragma: no cover - exercised via fallback tests
    Image = None
    ImageTk = None


_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS", None) if Image is not None else None


def _resolve_target_size(
    width: int,
    height: int,
    *,
    target_width: int | None = None,
    target_height: int | None = None,
) -> tuple[int, int]:
    """Fit the source size into the target box while preserving aspect ratio."""
    if width <= 0 or height <= 0:
        return (1, 1)

    limits: list[float] = [1.0]
    if target_width and target_width > 0:
        limits.append(float(target_width) / float(width))
    if target_height and target_height > 0:
        limits.append(float(target_height) / float(height))

    scale = min(limits)
    resized_width = max(1, int(round(width * scale)))
    resized_height = max(1, int(round(height * scale)))
    return (resized_width, resized_height)


def load_icon(
    path: str | Path,
    *,
    target_height: int | None = None,
    target_width: int | None = None,
    master: tk.Misc | None = None,
) -> tk.PhotoImage | None:
    """Load an image for Tk, preferring Pillow LANCZOS scaling."""
    image_path = Path(path)
    if not image_path.exists():
        return None

    if Image is not None and ImageTk is not None and _LANCZOS is not None:
        try:
            with Image.open(image_path) as source_image:
                prepared = source_image.convert("RGBA")
                resized_size = _resolve_target_size(
                    prepared.width,
                    prepared.height,
                    target_width=target_width,
                    target_height=target_height,
                )
                if resized_size != (prepared.width, prepared.height):
                    prepared = prepared.resize(resized_size, _LANCZOS)
                return ImageTk.PhotoImage(prepared, master=master)
        except Exception:
            pass

    try:
        image = tk.PhotoImage(master=master, file=str(image_path))
    except tk.TclError:
        return None

    resized_size = _resolve_target_size(
        image.width(),
        image.height(),
        target_width=target_width,
        target_height=target_height,
    )
    if resized_size == (image.width(), image.height()):
        return image

    scale = max(
        1,
        math.ceil(
            max(
                float(image.width()) / float(resized_size[0]),
                float(image.height()) / float(resized_size[1]),
            )
        ),
    )
    return image.subsample(scale, scale)
