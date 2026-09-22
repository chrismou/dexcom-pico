"""
Tests for the drawing helpers behind the trend arrow and the stale-state
"Previous:" corner.

Covers: draw_thick_line (solid strokes at any angle), draw_trend at the small
corner size, and draw_previous_corner layout (text shifts left only when an
arrow is drawn, and the arrow never overlaps the text).
"""
import unittest

import tests.hw_stubs as hw_stubs
hw_stubs.install()
import main


def _bresenham(x1, y1, x2, y2):
    """Yield the integer pixels PicoGraphics would light for a 1 px line."""
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    x, y = x1, y1
    while True:
        yield (x, y)
        if x == x2 and y == y2:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


class _SpyDisplay(unittest.TestCase):
    """Base class that records display.line and display.text calls."""

    def setUp(self):
        self.lines = []
        self.texts = []
        self._orig_line = main.display.line
        self._orig_text = main.display.text
        lines = self.lines
        texts = self.texts

        def _spy_line(x1, y1, x2, y2):
            lines.append((x1, y1, x2, y2))

        def _spy_text(text, x, y, wrap=main.WIDTH, scale=1):
            texts.append((text, x, y, scale))

        main.display.line = _spy_line
        main.display.text = _spy_text

    def tearDown(self):
        main.display.line = self._orig_line
        main.display.text = self._orig_text

    def pixels(self):
        pts = set()
        for x1, y1, x2, y2 in self.lines:
            pts.update(_bresenham(x1, y1, x2, y2))
        return pts


class TestDrawThickLine(_SpyDisplay):

    def test_horizontal_line_stacks_vertically(self):
        main.draw_thick_line(10, 20, 50, 20, 3)
        self.assertEqual(len(self.lines), 3)
        self.assertEqual(sorted(l[1] for l in self.lines), [19, 20, 21])
        self.assertTrue(all(l[0] == 10 and l[2] == 50 for l in self.lines))

    def test_vertical_line_stacks_horizontally(self):
        main.draw_thick_line(20, 10, 20, 50, 3)
        self.assertEqual(len(self.lines), 3)
        self.assertEqual(sorted(l[0] for l in self.lines), [19, 20, 21])

    def test_diagonal_line_has_no_gaps(self):
        """A 45-degree stroke must be a solid band, not hatched."""
        main.draw_thick_line(0, 0, 30, 30, 3)
        pts = self.pixels()
        rows = {}
        for x, y in pts:
            rows.setdefault(y, set()).add(x)
        for y in range(5, 26):
            xs = sorted(rows[y])
            self.assertEqual(xs, list(range(xs[0], xs[-1] + 1)), "gap in row %d" % y)
            self.assertGreaterEqual(len(xs), 3)

    def test_diagonal_uses_more_copies_than_thickness(self):
        main.draw_thick_line(0, 0, 30, 30, 3)
        self.assertEqual(len(self.lines), 4)

    def test_zero_length_draws_once(self):
        main.draw_thick_line(5, 5, 5, 5, 3)
        self.assertEqual(self.lines, [(5, 5, 5, 5)])


class TestDrawTrendSmall(_SpyDisplay):

    def _draw(self, trend):
        self.lines.clear()
        main.draw_trend(trend, 0, 0, main._PREV_ARROW_BOX_PX, main._PREV_ARROW_BOX_PX,
                        main.WHITE, size=main._PREV_ARROW_SIZE_PX,
                        thickness=main._PREV_ARROW_THICKNESS_PX)

    def test_every_arrow_trend_draws_something(self):
        for trend in main._ARROW_TRENDS:
            self._draw(trend)
            self.assertTrue(self.lines, trend)

    def test_unknown_trend_draws_nothing(self):
        for trend in (None, "NotComputable", "RateOutOfRange", "bogus"):
            self._draw(trend)
            self.assertEqual(self.lines, [], str(trend))

    def test_small_arrow_stays_near_its_box(self):
        box = main._PREV_ARROW_BOX_PX
        for trend in main._ARROW_TRENDS:
            self._draw(trend)
            for x, y in self.pixels():
                self.assertGreaterEqual(x, -3, trend)
                self.assertLessEqual(x, box + 2, trend)
                self.assertGreaterEqual(y, -3, trend)
                self.assertLessEqual(y, box + 2, trend)


class TestDrawPreviousCorner(_SpyDisplay):

    def test_no_arrow_when_trend_unknown(self):
        main.draw_previous_corner("Previous: 5.4", None)
        self.assertEqual(len(self.texts), 1)
        self.assertEqual(self.lines, [])
        text, x, y, scale = self.texts[0]
        self.assertEqual(scale, 2)
        self.assertEqual(x, main.WIDTH - 4 - main.display.measure_text(text, 2))

    def test_text_keeps_scale_two_with_arrow(self):
        main.draw_previous_corner("Previous: 5.4", "flat")
        self.assertEqual(self.texts[0][3], 2)

    def test_text_shifts_left_to_make_room_for_arrow(self):
        main.draw_previous_corner("Previous: 5.4", None)
        x_plain = self.texts[0][1]
        self.texts.clear()
        main.draw_previous_corner("Previous: 5.4", "flat")
        x_arrow = self.texts[0][1]
        self.assertEqual(x_plain - x_arrow, main._PREV_ARROW_BOX_PX + main._PREV_ARROW_GAP_PX)

    def test_arrow_sits_right_of_text_on_the_text_row(self):
        for trend in main._ARROW_TRENDS:
            self.texts.clear()
            self.lines.clear()
            main.draw_previous_corner("Previous: 22.2", trend)
            text, x, y, scale = self.texts[0]
            text_end = x + main.display.measure_text(text, scale)
            self.assertTrue(self.lines, trend)
            for px, py in self.pixels():
                self.assertGreaterEqual(px, text_end, trend)
                self.assertLess(px, main.WIDTH, trend)
                self.assertGreaterEqual(py, y - 8, trend)
                self.assertLess(py, main.HEIGHT, trend)


if __name__ == "__main__":
    unittest.main()
