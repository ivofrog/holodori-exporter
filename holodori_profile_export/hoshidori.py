"""Write a HoshiDori inventory file (the JSON that HoshiDori's Inventory -> "Import JSON" reads)
from the account data the game sent back.

hdb = holodori.best, hsdr = HoshiDori

What ends up in the file, and where each part comes from:
  cards           owned / bloom from the game; level from card EXP + limit breaks (see card_level)
  characterRanks  holomem ranks, from the game's own profile page data (Profile/GetUserProfileDetail)
  resources       cubes & hexas = what your boards have spent + what you currently hold in the game.
                  "spent" uses the node costs built into gamedata.py; "held" is your in-game items.
  holoboards      a second file (<id>.hoshidori-holoboards.json) for HoshiDori's Holoboard page ->
                  profile menu -> Import JSON. hsdr adds it as a new Holoboard profile; it never
                  overwrites an existing one.
  preset/defaults copied from an existing hsdr export if you pass one (--hoshidori-base); otherwise
                  left out, so hsdr's Import JSON keeps the selected profile's own presets.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


from . import gamedata, proto, schema, text


# Level cap for each limit-break count, by card rarity (same as the game's card data on
# holodori.best/game-data?tab=cards).
LEVEL_LIMITS = {3: [20, 30, 40, 50, 60], 4: [30, 40, 50, 60, 70], 5: [40, 50, 60, 70, 80]}

# Level from card EXP uses gamedata.LEVEL_EXP (the same curve for every rarity), capped by the
# card's current level limit (limit breaks).

# hsdr keeps these summer cards under placeholder IDs. Used unless your base file already uses the
# real ID.
SUMMER_PLACEHOLDERS = {
    "card-00023-5-uniq-0085-00": "card-upcoming-summer-marine",
    "card-00037-5-uniq-0086-00": "card-upcoming-summer-koyori",
    "card-03009-5-uniq-0088-00": "card-upcoming-summer-kobo",
    "card-04012-5-uniq-0087-00": "card-upcoming-summer-baelz",
    "card-00030-5-uniq-0089-00": "card-upcoming-summer-lamy",
    "card-00034-5-uniq-0090-00": "card-upcoming-summer-polka",
    "card-06003-5-uniq-0091-00": "card-upcoming-summer-ririka",
}

# hsdr resource keys (also the game's item IDs). -1 = cubes, -2 = hexas.
MATERIALS = [
    "item-skill_tree_allmember-1",  # green (Support)
    "item-skill_tree_allmember-2",
    "item-skill_tree_leader-1",  # red (Leader)
    "item-skill_tree_leader-2",
    "item-skill_tree_card-1",  # blue (Member)
    "item-skill_tree_card-2",
    "item-skill_tree_content-1",  # yellow (Content)
    "item-skill_tree_content-2",
]

BLANK_DEFAULTS = {
    "threeStarBloom": -1,
    "fiveStarBloom": -1,
    "fourStarBloom": -1,
    "characterRank": 1,
    "oshiCharacterId": "",
    "oshiCharacterRank": 1,
    "smallResourceQuantity": 0,
    "bigResourceQuantity": 0,
}


def card_key(cid: str, base_cards: dict | None = None) -> str:
    """The ID hsdr uses for a card: its placeholder for the summer cards, unless the base file uses the real ID."""
    ph = SUMMER_PLACEHOLDERS.get(cid)
    return ph if ph and cid not in (base_cards or {}) else cid


def _field(message: str, name: str, fallback: int) -> int:
    try:
        return schema.field(message, name)
    except RuntimeError:
        return fallback


def _rarity(card_id: str) -> int:
    parts = card_id.split("-")
    try:
        return int(parts[2])
    except (IndexError, ValueError):
        return 5


def card_level(card_id: str, exp: int, limit_breaks: int) -> tuple[int, bool]:
    """Return (level, exact). exact=False only if the card is past the end of the built-in EXP table
    (a future level cap above 80), where the level can't be worked out."""
    from bisect import bisect_right

    limits = LEVEL_LIMITS.get(_rarity(card_id), LEVEL_LIMITS[5])
    cap = limits[max(0, min(int(limit_breaks), len(limits) - 1))]
    table = gamedata.LEVEL_EXP
    level = bisect_right(table, int(exp))  # highest level whose total EXP is reached (1-based)
    if level >= len(table) and cap > len(table):
        return len(table), False
    return max(1, min(level, cap)), True


# ---------------------------------------------------------------- reading the game's account data


def user_cards(ud: dict[int, list]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for raw in proto.get_list(ud, schema.field("UserData", "user_card_list")):
        m = proto.decode(raw)
        cid = proto.get_str(m, schema.field("UserCard", "card_id"))
        if not cid:
            continue
        out[cid] = {
            "exp": proto.get_int(m, schema.field("UserCard", "exp")),
            "limitBreaks": proto.get_int(m, schema.field("UserCard", "level_limit_break_count")),
            "bloom": proto.get_int(m, schema.field("UserCard", "potential_upgrade_count")),
        }
    return out


def held_materials(ud: dict[int, list]) -> dict[str, int]:
    """Cubes/hexas currently in the in-game inventory (not yet spent on boards)."""
    held = {k: 0 for k in MATERIALS}
    item_id = schema.field("UserItem", "item_id")
    qty = schema.field("UserItem", "quantity")
    for raw in proto.get_list(ud, schema.field("UserData", "user_item_list")):
        m = proto.decode(raw)
        iid = proto.get_str(m, item_id)
        if iid in held:
            held[iid] += proto.get_int(m, qty)
    return held


def user_boards(ud: dict[int, list]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    chr_f = schema.field("UserCharacterSkillTree", "character_id")
    nodes_f = schema.field("UserCharacterSkillTree", "released_skill_tree_node_group_ids")
    for raw in proto.get_list(ud, schema.field("UserData", "user_character_skill_tree_list")):
        m = proto.decode(raw)
        cid = proto.get_str(m, chr_f)
        if cid:
            out[cid] = [
                v.decode("utf-8", "replace") for v in proto.get_list(m, nodes_f)
                if isinstance(v, (bytes, bytearray))
            ]
    return out


def user_board_builds(ud: dict[int, list], base_cards: dict | None = None) -> dict[str, dict]:
    """Each holomem's board as hsdr stores it: unlocked nodes + the card connected to each slot."""
    M = "UserCharacterSkillTree"
    chr_f = schema.field(M, "character_id")
    nodes_f = schema.field(M, "released_skill_tree_node_group_ids")
    slots_f = schema.field(M, "connected_skill_tree_node_group_ids")
    cards_f = schema.field(M, "connected_skill_tree_node_card_ids")
    dec = lambda vals: [v.decode("utf-8", "replace") for v in vals if isinstance(v, (bytes, bytearray))]
    out: dict[str, dict] = {}
    for raw in proto.get_list(ud, schema.field("UserData", "user_character_skill_tree_list")):
        m = proto.decode(raw)
        cid = proto.get_str(m, chr_f)
        if not cid:
            continue
        slots, conn = dec(proto.get_list(m, slots_f)), dec(proto.get_list(m, cards_f))
        out[cid] = {
            "selectedNodeIds": list(dict.fromkeys(dec(proto.get_list(m, nodes_f)))),
            "connectCards": {s: card_key(c, base_cards) for s, c in zip(slots, conn) if s and c},
        }
    return out


def player_name(ud: dict[int, list]) -> str:
    raw = proto.get(ud, schema.field("UserData", "user_profile"))
    u = proto.decode(raw) if isinstance(raw, (bytes, bytearray)) else {}
    return proto.get_str(u, schema.field("UserProfile", "name"))


def build_boards(characters: dict[str, dict], profile_name: str) -> dict:
    """hsdr's Holoboard export format (format "holodori-node-profiles", version 1)."""
    from datetime import datetime, timezone

    return {
        "format": "holodori-node-profiles",
        "version": 1,
        "exportedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "profiles": [{"name": profile_name, "characters": characters}],
    }


def public_user_id(ud: dict[int, list]) -> str:
    raw = proto.get(ud, schema.field("UserData", "user"))
    u = proto.decode(raw) if isinstance(raw, (bytes, bytearray)) else {}
    return proto.get_str(u, schema.field("User", "public_user_id"))


def parse_character_ranks(resp: dict[int, list]) -> dict[str, int]:
    """Profile/GetUserProfileDetail response -> {character_id: rank}."""
    info_raw = proto.get(resp, _field("ProfileGetUserProfileDetailResponse", "user_profile_detail_info", 1))
    info = proto.decode(info_raw) if isinstance(info_raw, (bytes, bytearray)) else {}
    cid_f = _field("CharacterLevel", "character_id", 1)
    lv_f = _field("CharacterLevel", "level", 2)
    ranks: dict[str, int] = {}
    for raw in proto.get_list(info, _field("UserProfileDetailInfo", "character_levels", 5)):
        m = proto.decode(raw)
        cid, lv = proto.get_str(m, cid_f), proto.get_int(m, lv_f)
        if cid and lv:
            ranks[cid] = lv
    return ranks


# ---------------------------------------------------------------- holoboard costs (built in, see gamedata.py)


def spent_on_boards(boards: dict[str, list[str]]) -> tuple[dict[str, int], list[str]]:
    """Cubes/hexas spent = cost of every unlocked node. Returns (spent, node IDs with no known cost)."""
    spent = {k: 0 for k in MATERIALS}
    unknown: list[str] = []
    for chr_id, nodes in boards.items():
        for node in nodes:
            cost = gamedata.NODE_COSTS.get(node)
            if cost is None:
                unknown.append(f"{chr_id}/{node}")
                continue
            for item, qty in cost.items():
                spent[item] += qty
    return spent, unknown


# ---------------------------------------------------------------- building the hsdr file


def build(
    cards: dict[str, dict],
    ranks: dict[str, int],
    *,
    all_card_ids: list[str] | None = None,
    spent: dict[str, int] | None = None,
    held: dict[str, int] | None = None,
    base: dict | None = None,
) -> tuple[dict, dict]:
    """Return (hsdr inventory JSON, report)."""
    base = base or {}
    base_cards: dict = dict(base.get("cards") or {})
    report: dict = {"uncertainLevels": [], "warnings": [], "owned": 0, "total": 0}

    def key_for(cid: str) -> str:
        return card_key(cid, base_cards)

    out_cards: dict[str, dict] = {}
    # Game cards are rebuilt from the account below; everything else in the base file (custom cards
    # you added in hsdr) is kept exactly as it was.
    game_keys = {key_for(cid) for cid in (all_card_ids or [])} | {key_for(cid) for cid in cards}
    game_keys |= {k for k in base_cards if k.startswith("card-") and k.split("-")[1].isdigit()}
    game_keys |= set(SUMMER_PLACEHOLDERS.values()) & set(base_cards)
    for k, v in base_cards.items():
        if k in game_keys and isinstance(v, dict):
            out_cards[k] = {**v, "owned": False}  # re-marked as owned below if the account has it
        else:
            out_cards[k] = v
    report["customCards"] = sorted(k for k in base_cards if k not in game_keys)
    for cid in all_card_ids or []:
        k = key_for(cid)
        if k not in out_cards:
            out_cards[k] = {"owned": False, "bloom": 0, "level": LEVEL_LIMITS.get(_rarity(cid), LEVEL_LIMITS[5])[-1]}

    for cid, c in cards.items():
        k = key_for(cid)
        level, exact = card_level(cid, c["exp"], c["limitBreaks"])
        out_cards[k] = {**(out_cards.get(k) or {}), "owned": True, "bloom": c["bloom"], "level": level}
        if not exact:
            report["uncertainLevels"].append({"card": cid, "exp": c["exp"], "limitBreaks": c["limitBreaks"], "levelWritten": level})

    # hsdr rejects the whole file if it names a card or holomem it doesn't know. An hsdr export lists
    # every card hsdr knows (custom ones included), so with a base file anything else is left out.
    if base_cards:
        unknown = [k for k in out_cards if k not in base_cards]
        for k in unknown:
            del out_cards[k]
        if unknown:
            report["warnings"].append(text.HSDR_WARN_UNKNOWN_CARDS.format(n=len(unknown)))
        report["skippedCards"] = unknown

    report["owned"] = sum(1 for v in out_cards.values() if isinstance(v, dict) and v.get("owned"))
    report["total"] = len(out_cards)
    if not all_card_ids and not base_cards:
        report["warnings"].append(text.HSDR_WARN_NO_CARD_LIST)

    base_ranks = dict(base.get("characterRanks") or {})
    out_ranks = dict(base_ranks)
    out_ranks.update(ranks)
    if base_ranks:
        skipped = [k for k in out_ranks if k not in base_ranks]
        for k in skipped:
            del out_ranks[k]
        if skipped:
            report["warnings"].append(text.HSDR_WARN_UNKNOWN_HOLOMEMS.format(n=len(skipped)))
    if not ranks:
        report["warnings"].append(text.HSDR_WARN_NO_RANKS)

    held = held or {k: 0 for k in MATERIALS}
    if spent is not None:
        resources = {k: spent.get(k, 0) + held.get(k, 0) for k in MATERIALS}
    elif base.get("resources"):
        resources = dict(base["resources"])
        report["warnings"].append(text.HSDR_WARN_NO_SPENT_BASE)
    else:
        resources = {k: held.get(k, 0) for k in MATERIALS}
        report["warnings"].append(text.HSDR_WARN_NO_SPENT)
    report["spent"], report["held"], report["resources"] = spent, held, resources

    # hsdr's Import JSON only replaces the sections a file includes and keeps the rest, so presets
    # and defaults are written only when they come from your own export (--hoshidori-base). Without
    # one they're left out, and hsdr keeps whatever the selected profile already has.
    # Always version 4: under version 3, hsdr refuses any file where a 3★ card is marked not owned.
    obj: dict = {"version": 4}
    if base.get("preset") is not None:
        obj["preset"] = base["preset"]
    if base.get("defaults"):
        obj["defaults"] = base["defaults"]
    obj["cards"] = out_cards
    obj["characterRanks"] = out_ranks
    obj["resources"] = resources
    return obj, report


def write(obj: dict, out_dir: str | Path, user_id: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{(user_id or 'profile').strip()}.hoshidori.json"
    dest.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def write_boards(obj: dict, out_dir: str | Path, user_id: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{(user_id or 'profile').strip()}.hoshidori-holoboards.json"
    dest.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def export(
    client,
    out_dir: str | Path,
    base_path: str | Path | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[Path, dict]:
    """Make the hsdr file from the account the client just downloaded (any source except mitm)."""
    ud = getattr(client, "user_data", None)
    if not ud:
        raise RuntimeError(text.HSDR_ERR_NO_DATA)
    uid = public_user_id(ud)

    ranks: dict[str, int] = {}
    echo(text.HSDR_STATUS_RANKS)
    try:
        ranks = client.profile_character_ranks(client.token, uid)
    except Exception as e:  # noqa: BLE001 - ranks are optional; say why they're missing
        echo(text.HSDR_WARN_RANKS_FAILED.format(error=e))
    return export_from(ud, ranks, out_dir, base_path)


def export_from(
    ud: dict[int, list],
    ranks: dict[str, int],
    out_dir: str | Path,
    base_path: str | Path | None = None,
) -> tuple[Path, dict]:
    """Make both hsdr files from an account snapshot (used by every source, mitm included)."""
    uid = public_user_id(ud)
    base = None
    if base_path:
        base = json.loads(Path(base_path).read_text(encoding="utf-8"))

    boards = user_boards(ud)
    spent, unknown_nodes = spent_on_boards(boards)
    cards = user_cards(ud)
    newer = sorted(c for c in cards if c not in gamedata.CARD_IDS_SET)
    obj, report = build(cards, ranks, all_card_ids=list(gamedata.CARD_IDS), spent=spent, held=held_materials(ud), base=base)
    if unknown_nodes:
        report["warnings"].insert(0, text.HSDR_WARN_UNKNOWN_NODES.format(n=len(unknown_nodes)))
    if newer and not base:
        report["warnings"].append(text.HSDR_WARN_NEWER_CARDS.format(n=len(newer)))
    report["unknownNodes"] = unknown_nodes
    dest = write(obj, out_dir, uid)
    report["dest"] = str(dest)
    report["ranks"] = len(ranks)

    # Holoboards: separate file for hsdr's Holoboard page (imported as a new profile)
    builds = user_board_builds(ud, (base or {}).get("cards"))
    if builds:
        from datetime import date

        name = f"{player_name(ud) or uid} \u00b7 game export {date.today().isoformat()}"
        bdest = write_boards(build_boards(builds, name), out_dir, uid)
        report["boardsDest"] = str(bdest)
        report["boards"] = {
            "profile": name,
            "holomems": len(builds),
            "nodes": sum(len(b["selectedNodeIds"]) for b in builds.values()),
        }
    return dest, report
