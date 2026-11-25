"""Chrome 主窗体、链接拖动提示层和菜单的窗口识别回归。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import native_windows


class BrowserWindowTests(unittest.TestCase):
    def test_unchanged_window_bounds_do_not_reapply_redraw_region(self):
        def rect(hwnd, pointer):
            pointer._obj.left = 0
            pointer._obj.top = 0
            pointer._obj.right = 1200
            pointer._obj.bottom = 800
            return True
        with patch.object(native_windows.user32, 'GetWindowRect', side_effect=rect), \
             patch.object(native_windows.gdi32, 'CreateRectRgn', return_value=99) as create, \
             patch.object(native_windows.user32, 'SetWindowRgn', return_value=True) as apply:
            bounds = native_windows.clip_titlebar(1, 32)
            self.assertEqual(bounds, (32, 1200, 800))
            self.assertEqual(native_windows.clip_titlebar(1, 32, bounds), bounds)
            create.assert_called_once_with(0, 32, 1200, 800)
            apply.assert_called_once()
    def accepted(self, style, extended):
        with patch.object(native_windows.user32, 'IsWindowVisible', return_value=True), patch.object(native_windows.user32, 'GetWindowLongW', side_effect=lambda handle, index: style if index == -16 else extended):
            return native_windows.is_browser_window(1)

    def test_app_windows_and_popup_pages_are_accepted(self):
        self.assertTrue(self.accepted(0x16CF0000, 0x00000100))
        self.assertTrue(self.accepted(0x96CF0000, 0x00000100))

    def test_drag_tooltip_and_menu_windows_are_not_pages(self):
        self.assertFalse(self.accepted(0x94080000, 0x08000088))
        self.assertFalse(self.accepted(0x90000000, 0x00000088))
        self.assertFalse(self.accepted(0x50000000, 0))


if __name__ == '__main__':
    unittest.main()
