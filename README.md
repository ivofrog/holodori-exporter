# holodori-profile-export
Export your **hololive Dreams** game account into a portable **`.hdbest` profile** you can import on
[holodori.best](https://holodori.best) as a profile, and (optionally) **HoshiDori** inventory and
holoboard files for HoshiDori.

This is a fork of [HolodoriDB/holodori-profile-export](https://github.com/HolodoriDB/holodori-profile-export)
that adds the HoshiDori export and a redesigned app.

> Last updated: v1.2.101 hololive Dreams · v5.8.0 holodori.best · v1.32.5 HoshiDori

The profile file is an unencrypted gzipped json file.

## Usage (Release)
Download the file for your system from the [latest release](../../releases/latest) and run it.

- **Windows:** `holodori-profile-export-windows.exe`.
- **macOS (Apple silicon):** unzip `holodori-profile-export-macos.zip`. The app isn't signed by Apple, so the
  first time, right-click it → **Open** → **Open** (or run `xattr -dr com.apple.quarantine "holodori exporter.app"`).
- **Linux:** `chmod +x holodori-profile-export-linux` once, then run it.

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
python main.py --source steam --hoshidori --hoshidori-base my-hoshidori-export.json   # keep your HoshiDori settings
python main.py --source steam --hoshidori --no-hdbest                                 # only the HoshiDori file
```

| HoshiDori field | where it comes from |
| --- | --- |
| cards (owned, bloom, level) | your game account. Level is worked out exactly from card EXP (the game's EXP-per-level table is built in) and capped by limit breaks. |
| characterRanks | the game's own profile data (`Profile/GetUserProfileDetail`). |
| resources (cubes & hexas) | what your boards have spent (node costs built into the exporter) **plus** what you hold in the game right now. |
| holoboards | a second file, `<user id>.hoshidori-holoboards.json`, for HoshiDori → Holoboard → profile menu (⋯) → **Import JSON**. HoshiDori adds it as a new Holoboard profile; existing ones are never overwritten. |
| preset / defaults | copied from `--hoshidori-base` if given; otherwise left out, so HoshiDori keeps the selected profile's own (its Import JSON only replaces sections the file contains). |
| custom cards | kept exactly (owned, bloom, level) from `--hoshidori-base`. Without a base file HoshiDori still keeps them, but resets them to its preset defaults (e.g. owned, B1, max level). |

HoshiDori's Import JSON rejects a whole file if it names a card or holomem it doesn't know. So:

- With `--hoshidori-base`, cards and holomems that aren't in that file (new ones HoshiDori hasn't added yet) are left out and listed as a warning.
- If you delete a custom card in HoshiDori after exporting your settings, export them again before running this; otherwise HoshiDori won't import the file.

Not available with `--source mitm`. Your login is only ever sent to the game servers. The card list
and holoboard node costs are built into the exporter (`gamedata.py`), so it makes no other requests.
Every holomem's board uses the same node costs, so new holomems are covered; if a game update adds
new nodes or cards, the done screen says so.

> [!NOTE]
> Every source except `mitm` signs in to the game servers, which signs the game out wherever it is open. Close the game first and reopen it afterwards.

## Sources (`--source`)

| source | platform | how |
| --- | --- | --- |
| `steam` | Windows (Steam) | Finds and reads the game's credential from your installed Steam game. |
| `mitm` | Any platform | Intercept the game's network traffic using mitm to read the user data request's response. |
| `transfer` | Any platform | Logs in with your in-game transfer id + password; log back in on your device afterward. |
| `credential` | Any platform | Logs in with a raw account credential you already have (advanced). |

### `steam`
```
python main.py --source steam
```
Launch the game on this PC and log in at least once. It reads the newest credential file in `%USERPROFILE%\AppData\LocalLow\QualiArts, Inc_\hololive-Dreams`, to download user data.

### `transfer`
```
python main.py --source transfer --migration-id <ID> --password <PW>
```
Logs in with transfer credentials (logs you out on your real device), then downloads your user data.

You can log in again by simplying restarting the game on your real device.

### `credential` (advanced)
```
python main.py --source credential --credential <ACCOUNT CREDENTIAL>
```
If you've somehow extracted your game credential (NOT TRANSFER ID/PASSWORD), you can use it directly.

### `mitm` (advanced)
```
pip install mitmproxy
mitmdump -s holodori_profile_export/sources/mitm.py --set hdbest_out=.
```
Route the game client through mitmproxy (trust its CA), then launch/relaunch the game (load into the game past the title screen!). When the home
screen loads, the script decodes the snapshot and writes the `.hdbest`.

Basic knowledge on how to intercept network requests (or use a proxy) is required.

> [!TIP]
> The `--emudhra` option found on newer versions of mitmproxy can bypass the need for you to trust the CA.
> 
> It is unknown how much longer this will work though, as Unity has released a fix in newer Unity versions that no longer trusts the leaked certificates from eMudhra, and emails were sent to all Unity developers.

*Easy for non-Android devices. Requires a rooted Android device if used on Android.