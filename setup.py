"""py2app build config. Produces a self-contained YT-sub.app under dist/.

  .venv/bin/python setup.py py2app
"""
from setuptools import setup

# Single source of truth for the version string.
exec(open('version.py').read())  # defines __version__

APP = ['app.py']

DATA_FILES = [
    ('skill', ['skill/SKILL.md']),
    ('assets', [
        'assets/yt_icon.png',
        'assets/yt_icon@2x.png',
        'assets/yt_icon.icns',
    ]),
]

OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'CFBundleName': 'YT-sub',
        'CFBundleDisplayName': 'YT-sub',
        'CFBundleIdentifier': 'com.brezhnev.yt-sub',
        'CFBundleVersion': __version__,
        'CFBundleShortVersionString': __version__,
        'LSUIElement': True,
        'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'MIT licensed',
    },
    'iconfile': 'assets/yt_icon.icns',
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
        # certifi provides the CA bundle for urllib's SSL context inside
        # the py2app bundle (otherwise CERTIFICATE_VERIFY_FAILED on any
        # https call from the frozen Python).
        'certifi',
    ],
    # `mcp_server` is a separate entry point (not imported by app.py), so
    # we explicitly include it to pull its imports into the graph too.
    'includes': [
        'icon', 'storage', 'stats', 'transcript', 'youtube_client',
        'mcp_server', 'config', 'version', 'web_metadata',
        'whisper_client', 'updater',
    ],
}

setup(
    app=APP,
    name='YT-sub',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
