"""Set the desktop graphics policy before Qt/WebEngine or QApplication starts."""
import os


def configure():
    # Quick's full-window software composition blocks the GUI thread during
    # scrolling and video playback. Use Qt's OpenGL RHI so WebEngine and Quick
    # select compatible accelerated surfaces, instead of the Windows default
    # D3D11 texture-sharing path. Native account Chrome keeps its own settings.
    # Keep software and the platform default available for driver diagnostics.
    mode = os.environ.get('MEDIA_ASSISTANT_RENDERER', 'opengl').lower()
    if mode not in ('opengl', 'software', 'hardware'):
        mode = 'opengl'
    if mode == 'opengl':
        os.environ.setdefault('QSG_RHI_BACKEND', 'opengl')
    if mode == 'software':
        os.environ['QT_QUICK_BACKEND'] = 'software'
        flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
        for flag in ('--disable-gpu',):
            if flag not in flags.split():
                flags = (flags + ' ' + flag).strip()
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = flags
    return mode
