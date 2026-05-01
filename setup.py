"""py2app build config. Produces a self-contained YT-sub.app under dist/.

  .venv/bin/python setup.py py2app
"""
from setuptools import setup

APP = ['app.py']

DATA_FILES = [
    ('skill', ['skill/SKILL.md']),
    ('assets', ['assets/yt_icon.png', 'assets/yt_icon.icns']),
]

OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'CFBundleName': 'YT-sub',
        'CFBundleDisplayName': 'YT-sub',
        'CFBundleIdentifier': 'com.brezhnev.yt-sub',
        'CFBundleVersion': '0.1.1',
        'CFBundleShortVersionString': '0.1.1',
        'LSUIElement': True,
        'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'MIT licensed',
    },
    'iconfile': 'assets/yt_icon.icns',
    # py2app needs help finding lazy-imported subpackages
    # py2app traces the import graph from app.py + listed includes, but
    # several deps use import_module() / lazy imports it can't see.
    # Include them as full packages so every submodule is bundled.
    'packages': [
        'anyio',
        'mcp',
        'charset_normalizer',
        'idna',
        'pydantic',
        'pydantic_core',
        'httpx',
        'httpcore',
    ],
    # `mcp_server` is a separate entry point (not imported by app.py), so
    # we explicitly include it to pull its imports into the graph too.
    'includes': [
        'icon', 'storage', 'stats', 'transcript', 'youtube_client',
        'mcp_server',
    ],
}

setup(
    app=APP,
    name='YT-sub',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
