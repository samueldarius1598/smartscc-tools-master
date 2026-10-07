import unittest
from types import SimpleNamespace

from smartscc_tools.widgets.treeview_scroll import bind_treeview_scroll_support


class _FakeTree:
    def __init__(self) -> None:
        self.bindings: dict[str, object] = {}
        self.y_calls: list[tuple[int, str]] = []
        self.x_calls: list[tuple[int, str]] = []

    def bind(self, sequence, callback, add=None):  # noqa: ANN001, ANN201
        self.bindings[sequence] = callback
        return f"bind:{sequence}:{add}"

    def yview_scroll(self, amount: int, units: str) -> None:
        self.y_calls.append((amount, units))

    def xview_scroll(self, amount: int, units: str) -> None:
        self.x_calls.append((amount, units))


class TreeviewScrollSupportTest(unittest.TestCase):
    def test_mousewheel_support_binds_vertical_and_horizontal_sequences(self) -> None:
        tree = bind_treeview_scroll_support(_FakeTree())

        self.assertIn("<MouseWheel>", tree.bindings)
        self.assertIn("<Shift-MouseWheel>", tree.bindings)
        self.assertIn("<Button-4>", tree.bindings)
        self.assertIn("<Button-5>", tree.bindings)

    def test_vertical_mousewheel_scrolls_treeview(self) -> None:
        tree = bind_treeview_scroll_support(_FakeTree())

        result = tree.bindings["<MouseWheel>"](SimpleNamespace(delta=120))

        self.assertEqual(result, "break")
        self.assertEqual(tree.y_calls, [(-1, "units")])

    def test_touchpad_deltas_accumulate_before_scrolling(self) -> None:
        tree = bind_treeview_scroll_support(_FakeTree())

        tree.bindings["<MouseWheel>"](SimpleNamespace(delta=40))
        tree.bindings["<MouseWheel>"](SimpleNamespace(delta=40))
        tree.bindings["<MouseWheel>"](SimpleNamespace(delta=40))

        self.assertEqual(tree.y_calls, [(-1, "units")])

    def test_shift_mousewheel_scrolls_horizontally(self) -> None:
        tree = bind_treeview_scroll_support(_FakeTree())

        result = tree.bindings["<Shift-MouseWheel>"](SimpleNamespace(delta=-120))

        self.assertEqual(result, "break")
        self.assertEqual(tree.x_calls, [(1, "units")])


if __name__ == "__main__":
    unittest.main()
