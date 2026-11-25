"""Desktop startup graphics policy is deterministic across both entry points."""
import os
import unittest
from unittest.mock import patch
from core.desktop_rendering import configure


class DesktopRenderingTests(unittest.TestCase):
    def test_default_acceleration_does_not_disable_chromium_gpu(self):
        with patch.dict(os.environ, {'QTWEBENGINE_CHROMIUM_FLAGS': '--remote-debugging-port=12345'}, clear=True):
            self.assertEqual(configure(), 'opengl')
            self.assertEqual(os.environ['QSG_RHI_BACKEND'], 'opengl')
            self.assertNotIn('QT_QUICK_BACKEND', os.environ)
            self.assertEqual(os.environ['QTWEBENGINE_CHROMIUM_FLAGS'], '--remote-debugging-port=12345')
            self.assertEqual(configure(), 'opengl')

    def test_explicit_software_policy_keeps_other_chromium_flags(self):
        with patch.dict(os.environ, {'MEDIA_ASSISTANT_RENDERER': 'software', 'QTWEBENGINE_CHROMIUM_FLAGS': '--remote-debugging-port=12345'}, clear=True):
            self.assertEqual(configure(), 'software')
            self.assertEqual(os.environ['QT_QUICK_BACKEND'], 'software')
            self.assertIn('--remote-debugging-port=12345', os.environ['QTWEBENGINE_CHROMIUM_FLAGS'])
            configure()
            self.assertEqual(os.environ['QTWEBENGINE_CHROMIUM_FLAGS'].split().count('--disable-gpu'), 1)

    def test_explicit_diagnostic_mode_and_invalid_mode(self):
        with patch.dict(os.environ, {'MEDIA_ASSISTANT_RENDERER': 'hardware'}, clear=True):
            self.assertEqual(configure(), 'hardware')
            self.assertNotIn('QT_QUICK_BACKEND', os.environ)
        with patch.dict(os.environ, {'MEDIA_ASSISTANT_RENDERER': 'invalid'}, clear=True):
            self.assertEqual(configure(), 'opengl')

    def test_explicit_qt_backend_is_preserved(self):
        with patch.dict(os.environ, {'QSG_RHI_BACKEND': 'd3d12'}, clear=True):
            configure()
            self.assertEqual(os.environ['QSG_RHI_BACKEND'], 'd3d12')

