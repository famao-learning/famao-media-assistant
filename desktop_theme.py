"""Native controls use the same dark surfaces and accent as the publishing UI."""
NATIVE_THEME = """
QWidget { background:#17181f; color:#f2f2f5; font-family:'Microsoft YaHei'; font-size:12px; }
QMainWindow, QStatusBar { background:#101116; }
QStatusBar { color:#a2a3af; border-top:1px solid #2c2d37; padding:2px 10px; }
QToolBar { background:#17181f; border:0; border-bottom:1px solid #2c2d37; }
QPushButton { min-height:34px; padding:0 14px; border:1px solid #8993a3; border-radius:8px; background:#2a303b; color:#f2f5f9; }
QPushButton:hover { background:#394250; border-color:#c1cede; }
QPushButton:focus { border-color:#fe2c55; }
QPushButton#returnPublisher { background:#dc1746; color:white; border-color:#ff718e; font-weight:600; }
QPushButton#returnPublisher:hover { background:#be123b; }
QPushButton:disabled { background:#414a58; color:#c2cbd8; border-color:#667386; }
QComboBox { min-height:34px; padding:0 12px; border:1px solid #8993a3; border-radius:8px; background:#2a303b; color:#f2f5f9; }
QComboBox QAbstractItemView { background:#22232c; color:#f2f2f5; selection-background-color:#55303e; selection-color:white; border:1px solid #444550; padding:6px; }
QLabel { color:#b7b8c2; }
QMessageBox QLabel { min-width:360px; line-height:1.6; }
QToolTip { background:#30313d; color:white; border:1px solid #4b4c58; padding:6px; }
"""
