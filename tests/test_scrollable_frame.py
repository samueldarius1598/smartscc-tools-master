import unittest
from types import SimpleNamespace

from smartscc_tools.widgets.scrollable_frame import ScrollableFrame


class _FakeWidget:
    def __init__(self, widget_class: str, master=None) -> None:
        self._widget_class = widget_class
        self.master = master

    def winfo_class(self) -> str:
        return self._widget_class


class _FakeCanvas(_FakeWidget):
    def __init__(self, view: tuple[float, float]) -> None:
        super().__init__("Canvas")
        self._view = view
        self.scroll_calls: list[tuple[int, str]] = []

    def yview(self) -> tuple[float, float]:
        return self._view

    def yview_scroll(self, amount: int, units: str) -> None:
        self.scroll_calls.append((amount, units))


class ScrollableFrameTest(unittest.TestCase):
    def _build_frame(self, *, view: tuple[float, float] = (0.2, 0.8)) -> tuple[ScrollableFrame, _FakeWidget]:
        frame = ScrollableFrame.__new__(ScrollableFrame)
        frame._mousewheel_remainder = 0
        frame._contains_cache: dict[int, bool] = {}
        frame._ignore_cache: dict[int, bool] = {}
        frame.canvas = _FakeCanvas(view)
        frame.interior = _FakeWidget("Frame", master=frame)
        frame.winfo_exists = lambda: True
        child = _FakeWidget("TLabel", master=frame.interior)
        return frame, child

    def test_touchpad_deltas_accumulate_before_scrolling(self) -> None:
        frame, child = self._build_frame()

        result_a = frame._on_mousewheel(SimpleNamespace(widget=child, delta=40))
        result_b = frame._on_mousewheel(SimpleNamespace(widget=child, delta=40))
        result_c = frame._on_mousewheel(SimpleNamespace(widget=child, delta=40))

        self.assertEqual(result_a, "break")
        self.assertEqual(result_b, "break")
        self.assertEqual(result_c, "break")
        self.assertEqual(frame.canvas.scroll_calls, [(-1, "units")])
        self.assertEqual(frame._mousewheel_remainder, 0)

    def test_ignored_inner_text_widget_keeps_own_scroll_behavior(self) -> None:
        frame, _child = self._build_frame()
        text_widget = _FakeWidget("Text", master=frame.interior)

        result = frame._on_mousewheel(SimpleNamespace(widget=text_widget, delta=-120))

        self.assertIsNone(result)
        self.assertEqual(frame.canvas.scroll_calls, [])

    def test_boundary_scroll_resets_remainder_without_scrolling(self) -> None:
        frame, child = self._build_frame(view=(0.0, 0.6))

        result = frame._on_mousewheel(SimpleNamespace(widget=child, delta=120))

        self.assertIsNone(result)
        self.assertEqual(frame.canvas.scroll_calls, [])
        self.assertEqual(frame._mousewheel_remainder, 0)

    def test_linux_button_events_map_to_standard_wheel_step(self) -> None:
        self.assertEqual(
            ScrollableFrame._raw_mousewheel_delta(SimpleNamespace(num=4, delta=0)),
            ScrollableFrame.WHEEL_DELTA_STEP,
        )
        self.assertEqual(
            ScrollableFrame._raw_mousewheel_delta(SimpleNamespace(num=5, delta=0)),
            -ScrollableFrame.WHEEL_DELTA_STEP,
        )


if __name__ == "__main__":
    unittest.main()
