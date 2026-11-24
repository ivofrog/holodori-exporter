from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from .. import profile, text
from ..client import GameClient

_REL = Path("AppData/LocalLow/QualiArts, Inc_/hololive-Dreams")
STEAM_APP_ID = "4282500"  # hololive Dreams on Steam


def _windows_default() -> Path:
    base = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(base) / _REL


def _steam_roots() -> list[Path]:
    """Where Steam itself usually lives on Linux (native, Flatpak and Snap installs)."""
    home = Path.home()
    return [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / "snap/steam/common/.local/share/Steam",
    ]


def _steam_libraries(root: Path) -> list[Path]:
    """The Steam root plus any extra library folders listed in libraryfolders.vdf (other drives)."""
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text("utf-8", errors="replace")):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    return libs


def _wine_prefixes() -> list[Path]:
    """Wine-style C: drives where the Windows game may run: CrossOver and Whisky bottles on macOS,
    plain Wine on macOS/Linux. Their user folder name varies (crossover, steamuser, your name...)."""
    home = Path.home()
    drives: list[Path] = []
    if sys.platform == "darwin":
        for bottles in (
            home / "Library/Application Support/CrossOver/Bottles",
            home / "Library/Containers/com.isaacmarovitz.Whisky/Bottles",
        ):
            try:
                drives += sorted(b / "drive_c" for b in bottles.iterdir() if b.is_dir())
            except OSError:
                pass
    drives.append(home / ".wine/drive_c")
    found: list[Path] = []
    for drive in drives:
        try:
            found += sorted(u / _REL for u in (drive / "users").iterdir() if u.is_dir())
        except OSError:
            pass
    return found


def candidates() -> list[Path]:
    """Folders that may hold the game's saved sign-in, most likely first."""
    if sys.platform == "win32":
        return [_windows_default()]
    out: list[Path] = []
    # Linux: the Windows game runs through Proton, which keeps its own Windows-style folder per game
    if sys.platform != "darwin":
        for root in _steam_roots():
            for lib in _steam_libraries(root):
                out.append(
                    lib / "steamapps/compatdata" / STEAM_APP_ID / "pfx/drive_c/users/steamuser" / _REL
                )
    out += _wine_prefixes()  # macOS: CrossOver / Whisky; both: plain Wine
    out.append(Path.home() / _REL)  # a copied folder in the home directory still works
    seen: set[str] = set()
    unique = []
    for p in out:
        key = str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def game_data_dir(override: str | Path | None = None) -> Path:
    """The folder to read the saved sign-in from: the one given, else the first that exists."""
    if override:
        return Path(override).expanduser()
    found = candidates()
    for p in found:
        if p.is_dir():
            return p
    return found[0]


def read_credential(folder: str | Path | None = None) -> str:
    root = game_data_dir(folder)
    if not root.is_dir():
        raise FileNotFoundError(text.ERR_STEAM_NO_FOLDER.format(root=root))
    dc = sorted(
        (p for p in root.iterdir() if p.is_file() and p.name.startswith("DC")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not dc:
        raise FileNotFoundError(text.ERR_STEAM_NO_FILE.format(root=root))
    cred = dc[0].read_text("utf-8-sig").replace("﻿", "").strip()
    if not cred:
        raise ValueError(text.ERR_STEAM_EMPTY.format(name=dc[0].name))
    return cred


def run(
    client: GameClient,
    out_dir: str,
    echo: Callable[[str], None] = print,
    hdbest: bool = True,
    data_dir: str | Path | None = None,
) -> tuple[dict, Path | None]:
    cred = read_credential(data_dir)
    echo(text.STATUS_STEAM_LOGIN)
    ud = client.user_get(client.auth_login(cred))
    obj = profile.build(ud)
    dest = profile.write(obj, out_dir) if hdbest else None  # off when only the HoshiDori file is wanted
    return obj, dest
