# holodori exporter
Export your **hololive Dreams** game account into a portable **`.hdbest` profile** you can import on
[holodori.best](https://holodori.best) as a profile, and **HoshiDori** inventory + holoboard files for HoshiDori.

This is a fork of [HolodoriDB/holodori-profile-export](https://github.com/HolodoriDB/holodori-profile-export)
that adds the HoshiDori export and a redesigned app.

> Last updated: v1.3.0 hololive Dreams · v5.9.0 holodori.best · v1.33.1 HoshiDori

The profile file is an unencrypted gzipped json file.

## Usage (Release)
Download the file for your system from the [latest release](../../releases/latest) and run it.

- **Windows:** unzip `holodori-exporter-windows.zip` and run **holodori exporter.exe**.
- **macOS (Apple silicon):** unzip `holodori-exporter-macos.zip`. The app isn't signed by Apple, so the
  first time, right-click **holodori exporter** → **Open** → **Open** (or run `xattr -dr com.apple.quarantine "holodori exporter.app"`).
- **Linux:** unzip `holodori-exporter-linux.zip` and run **holodori exporter** (if it won't start, run `chmod +x "holodori exporter"` once).

## Usage (Python)
Download the repository as a zip, then unzip it. Python 3.10+ is required (and should be in path).

```bash
pip install -r requirements.txt
python main.py # run the gui
python main.py --source <source> # run with CLI
```

It will create a `<user id>.hdbest` file. Upload that on holodori.best to import your account data (My Game Data -> Profiles -> click "Update" on a profile).

## HoshiDori inventory file (`--hoshidori`)
Also writes `<user id>.hoshidori.json` (for HoshiDori **Inventory → Import JSON**) and `<user id>.hoshidori-holoboards.json` (for **Holoboard → ⋯ → Import JSON**).
In the window app, use the **Export for** switches (holodori.best and HoshiDori, both on by default). Each file is always complete.

```bash
python main.py --source steam --hoshidori
python main.py --source steam --hoshidori --hoshidori-base my-hoshidori-export.json   # keep your custom cards and presets
python main.py --source steam --hoshidori --no-hdbest                                 # only the HoshiDori file
```

| HoshiDori field | where it comes from |
| --- | --- |
| cards (owned, bloom, level) | your game account. Level is worked out exactly from card EXP (the game's EXP-per-level table is built in) and capped by limit breaks. |
| characterRanks | the game's own profile data (`Profile/GetUserProfileDetail`). |
| resources (cubes & hexas) | what your boards have spent (node costs built into the exporter) **plus** what you hold in the game right now. |
| memoryBonusPercentage | worked out from the memories (posters) you have collected. |
| holoboards | a second file, `<user id>.hoshidori-holoboards.json`, for HoshiDori → Holoboard → profile menu (⋯) → **Import JSON**. HoshiDori adds it as a new Holoboard profile; existing ones are never overwritten. |
| preset / defaults | copied from `--hoshidori-base` if given; otherwise left out, so HoshiDori keeps the selected profile's own (its Import JSON only replaces sections the file contains). |
| custom cards | kept exactly (owned, bloom, level) from `--hoshidori-base`. Without a base file, HoshiDori still keeps them, but resets them to its preset defaults (e.g. owned, B1, max level). |

HoshiDori's Import JSON rejects a whole file if it names a card or holomem it doesn't know. So:

- With `--hoshidori-base`, cards and holomems that aren't in that file (new ones HoshiDori hasn't added yet) are left out and listed as a warning.
- If you delete a custom card in HoshiDori after exporting your inventory, export it again before running this; otherwise HoshiDori won't import the file.

Your login is only ever sent to the game servers. The card list
and holoboard node costs are built into the exporter (`gamedata.py`), so it makes no other requests.
Every holomem's board uses the same node costs, so new holomems are covered; if a game update adds
new nodes or cards, the done screen says so.

> [!NOTE]
> Signing in (Steam, transfer or credential) signs the game out wherever it is open. Close the game first and reopen it afterwards.

## Sources (`--source`)

| source | platform | how |
| --- | --- | --- |
| `steam` | Windows, Linux (Proton), macOS (CrossOver / Whisky) | Finds and reads the game's saved sign-in from the Steam version on this computer. |
| `transfer` | Any platform | Logs in with your in-game transfer id + password; log back in on your device afterward. |
| `credential` | Any platform | Logs in with a raw account credential you already have (advanced). |

There's also a command-line-only `mitm` source (which may no longer work with current versions of the game), see [Advanced: command line only](#advanced-command-line-only).

### `steam`
```
python main.py --source steam
```
Launch the game on this PC and log in at least once. It reads the newest credential file in the game's data folder, to download user data:

- **Windows:** `%USERPROFILE%\AppData\LocalLow\QualiArts, Inc_\hololive-Dreams`
- **Linux (Steam / Proton):** found automatically in Steam's Proton folder for the game, including extra Steam library drives, e.g. `~/.steam/steam/steamapps/compatdata/4282500/pfx/drive_c/users/steamuser/AppData/LocalLow/QualiArts, Inc_/hololive-Dreams`
- **macOS (CrossOver / Whisky):** found automatically inside your CrossOver or Whisky bottles (and plain Wine).

If it's somewhere else, pick the folder in the app (**Change…** next to *Game data folder*) or pass `--steam-folder "<path to the hololive-Dreams folder>"`.

The app remembers your region, save-to folder and game data folder for next time, in a small `settings.json`
(Windows: `%APPDATA%\holodori-exporter`, macOS: `~/Library/Application Support/holodori-exporter`,
Linux: `~/.config/holodori-exporter`). It never stores a sign-in or any account data.

### `transfer`
```
python main.py --source transfer --migration-id <ID> --password <PW>
```
Logs in with transfer credentials (logs you out on your real device), then downloads your user data.

You can log in again by simply restarting the game on your real device.

### `credential` (advanced)
```
python main.py --source credential --credential <ACCOUNT CREDENTIAL>
```
If you've somehow extracted your game credential (NOT TRANSFER ID/PASSWORD), you can use it directly.

## Advanced: command line only

These aren't in the app; they're only available from a terminal.

### `mitm`

> [!NOTE]
> This source was added upstream, so I decided to keep it in this fork.
>
> Ranks would be included when you visit your profile in-game, and left out (with a warning) otherwise.

Reads your account by watching the game's own network traffic, instead of signing in, so the game is **not** signed out.

```
pip install mitmproxy
mitmdump -s holodori_profile_export/sources/mitm.py --set hdbest_out=.
```
Route the game client through mitmproxy (trust its CA), then launch/relaunch the game (load into the game past the title screen!). When the home
screen loads, the script decodes the snapshot and writes the `.hdbest`.

To also make the HoshiDori files, add `--set hoshidori=true` (and `--set hoshidori_base=<file>` to build on your current HoshiDori inventory and keep custom cards; `--set no_hdbest=true` skips the `.hdbest`):

```
mitmdump -s holodori_profile_export/sources/mitm.py --set hdbest_out=. --set hoshidori=true
```

Basic knowledge on how to intercept network requests (or use a proxy) is required.

> [!WARNING]
> **This may no longer work.** The game ignores the certificates trusted on your device, so it rejects mitmproxy's certificate.
> The `--emudhra` option found on newer versions of mitmproxy used to get around this, but Unity has released a fix that no longer trusts the leaked eMudhra certificates, and current versions of the game reject it too.
> Use the `steam` or `transfer` source instead.

*Easy for non-Android devices. Requires a rooted Android device if used on Android.

## Changelog

### 1.2.1 (bug fix)
- **Fixed:** exporting without **Import HoshiDori Inventory** reset your HoshiDori defaults (oshi, ranks and materials). Defaults are now only written when you import your HoshiDori inventory.
- Cards newer than the exporter are marked not owned when you use **Import HoshiDori Inventory**.
- When HoshiDori adds the upgrade bonus to its exports, it's carried over from your imported HoshiDori inventory automatically.

### 1.2.0
- Made minor visual tweaks/adjustments.
- Added the two new holoboard nodes (including the new top Leader node) and the new Red Star Cubes to the HoshiDori export.
- The HoshiDori inventory file now includes your memory bonus, worked out from the memories (posters) you have collected. It uses HoshiDori's current file layout.
- **Fixed:** cards newer than the exporter no longer show as owned in HoshiDori.
- **Fixed:** importing your current HoshiDori inventory works with HoshiDori's newer export files (custom cards, preset and defaults were being left out).

### 1.1.2
- **Steam on Linux and macOS:** the game's sign-in is now found automatically on Linux (Steam / Proton, including
  extra Steam library drives) and on macOS (CrossOver, Whisky or plain Wine).
- **Pick the game data folder:** if it's somewhere else, choose it with **Change…** in the app, or pass `--steam-folder`.
- **Remembers your settings:** region, save-to folder and game data folder are kept for next time (never your sign-in).
- **`mitm` can make HoshiDori files** (`--set hoshidori=true`). Ranks are added once you open your profile screen in the game.
- The Steam step now says it exports the account you last signed in with.
- A rejected transfer ID/password now says what to check, instead of `AccountMigration/Migrate failed (grpc-status=3)`.
- **Fixed:** the window could be resized smaller than its content, cutting off the bottom; text now wraps to fit.
- **Fixed:** toggles and sign-in icons looked blurry or clipped on macOS; they're now drawn sharp and centered.
- Tweaked interface colors and adjusted text descriptions for clarity. The title bar now blends into the app on macOS and Windows 11 (dark on Windows 10).

### 1.1.1 (bug fix)
- **Fixed:** cards below their level cap were exported with the wrong level (often their max level) and a
  "couldn't be read exactly" warning. Levels are now worked out exactly from card EXP for every card.

### 1.1.0
- HoshiDori export: inventory (cards with bloom and level, holomem ranks, cubes/hexas) and a holoboards file.
- "Import current settings" keeps your HoshiDori custom cards and presets.
- Card list and holoboard node costs built in, so no extra web requests.
- Redesigned app.
