import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from smartscc_tools.widgets import image_utils


class _FakePillowImage:
    def __init__(self, size: tuple[int, int]) -> None:
        self.width, self.height = size
        self.size = size
        self.convert_mode: str | None = None
        self.resize_calls: list[tuple[tuple[int, int], object]] = []

    def __enter__(self) -> "_FakePillowImage":
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> bool:
        return False

    def convert(self, mode: str) -> "_FakePillowImage":
        self.convert_mode = mode
        return self

    def resize(self, size: tuple[int, int], resample: object) -> "_FakePillowImage":
        self.resize_calls.append((size, resample))
        return _FakePillowImage(size)


class _FakeTkPhoto:
    def __init__(self, width: int, height: int, scaled: object) -> None:
        self._width = width
        self._height = height
        self._scaled = scaled
        self.subsample_calls: list[tuple[int, int]] = []

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def subsample(self, x_scale: int, y_scale: int) -> object:
        self.subsample_calls.append((x_scale, y_scale))
        return self._scaled


class ImageUtilsTest(unittest.TestCase):
    def test_load_icon_uses_pillow_lanczos_resize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "icon.png"
            image_path.write_bytes(b"fake")
            fake_image = _FakePillowImage((200, 100))
            fake_image_module = SimpleNamespace(open=mock.Mock(return_value=fake_image))
            fake_imagetk_module = SimpleNamespace(PhotoImage=mock.Mock(return_value="photo"))

            with mock.patch.object(image_utils, "Image", fake_image_module), mock.patch.object(
                image_utils, "ImageTk", fake_imagetk_module
            ), mock.patch.object(image_utils, "_LANCZOS", "LANCZOS"):
                result = image_utils.load_icon(image_path, target_height=25, master="root")

        self.assertEqual(result, "photo")
        self.assertEqual(fake_image.convert_mode, "RGBA")
        self.assertEqual(fake_image.resize_calls, [((50, 25), "LANCZOS")])
        fake_imagetk_module.PhotoImage.assert_called_once()
        self.assertEqual(fake_imagetk_module.PhotoImage.call_args.args[0].size, (50, 25))
        self.assertEqual(fake_imagetk_module.PhotoImage.call_args.kwargs["master"], "root")

    def test_load_icon_preserves_aspect_ratio_without_upscale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "icon.png"
            image_path.write_bytes(b"fake")
            fake_image = _FakePillowImage((40, 20))
            fake_image_module = SimpleNamespace(open=mock.Mock(return_value=fake_image))
            fake_imagetk_module = SimpleNamespace(PhotoImage=mock.Mock(return_value="photo"))

            with mock.patch.object(image_utils, "Image", fake_image_module), mock.patch.object(
                image_utils, "ImageTk", fake_imagetk_module
            ), mock.patch.object(image_utils, "_LANCZOS", "LANCZOS"):
                result = image_utils.load_icon(image_path, target_width=100, target_height=100)

        self.assertEqual(result, "photo")
        self.assertEqual(fake_image.resize_calls, [])
        fake_imagetk_module.PhotoImage.assert_called_once()
        self.assertEqual(fake_imagetk_module.PhotoImage.call_args.args[0].size, (40, 20))

    def test_load_icon_falls_back_to_photoimage_subsample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "icon.png"
            image_path.write_bytes(b"fake")
            scaled_photo = object()
            fake_photo = _FakeTkPhoto(width=315, height=318, scaled=scaled_photo)

            with mock.patch.object(image_utils, "Image", None), mock.patch.object(image_utils, "ImageTk", None), mock.patch.object(
                image_utils, "_LANCZOS", None
            ), mock.patch.object(image_utils.tk, "PhotoImage", return_value=fake_photo) as photo_image:
                result = image_utils.load_icon(image_path, target_height=36, master="root")

        self.assertIs(result, scaled_photo)
        photo_image.assert_called_once_with(master="root", file=str(image_path))
        self.assertEqual(fake_photo.subsample_calls, [(9, 9)])


if __name__ == "__main__":
    unittest.main()
