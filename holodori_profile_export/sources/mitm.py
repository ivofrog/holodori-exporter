"""mitmproxy addon: observe the game client's traffic and write the .hdbest (and, if asked, the
HoshiDori files) from the account snapshot it sees. non-destructive. see the README for how to run it."""

from __future__ import annotations

import os

from holodori_asset_tools import api

from .. import hoshidori, profile, proto, schema, text
from ..client import require_asset_tools

_HOST = "game-hololive-dreams.com"
_PROFILE_DETAIL = "Profile/GetUserProfileDetail"


class HdbestCapture:
    def __init__(self) -> None:
        require_asset_tools()
        self.done = False
        self.ud: dict[int, list] | None = None  # snapshot kept for the HoshiDori rewrite
        self.ranks: dict[str, int] = {}

    def load(self, loader) -> None:  # mitmproxy hook
        loader.add_option("hdbest_out", str, os.getcwd(), text.MITM_OUT_OPTION)
        loader.add_option("hoshidori", bool, False, text.MITM_HSDR_OPTION)
        loader.add_option("hoshidori_base", str, "", text.MITM_HSDR_BASE_OPTION)
        loader.add_option("no_hdbest", bool, False, text.MITM_NO_HDBEST_OPTION)

    def _userdata_from(self, path: str, body: bytes) -> dict[int, list] | None:
        resp = proto.decode(api.decode_response(body))
        if path.endswith("User/Get"):
            ud = proto.get(resp, schema.field("UserGetResponse", "user_data"))
        elif path.endswith("Home/Login"):
            common = proto.get(
                resp, schema.field("HomeLoginResponse", "common_response")
            )
            cm = proto.decode(common) if isinstance(common, (bytes, bytearray)) else {}
            ud = proto.get(cm, schema.field("Response", "updated_data"))
        else:
            return None
        return proto.decode(ud) if isinstance(ud, (bytes, bytearray)) else None

    def _write_hoshidori(self, first: bool) -> None:
        dest, report = hoshidori.export_from(
            self.ud, self.ranks, ctx_out(), opt("hoshidori_base") or None
        )
        if not first:
            print(text.MITM_HSDR_RANKS_ADDED.format(ranks=len(self.ranks), dest=dest))
            return
        print(text.MITM_HSDR_CAPTURED.format(dest=dest, ranks=len(self.ranks)))
        if report.get("boardsDest"):
            print(text.MITM_HSDR_BOARDS.format(dest=report["boardsDest"]))
        for w in report.get("warnings", []):
            if w == text.HSDR_WARN_NO_RANKS:
                continue  # the mitm note below says what to do instead
            print(text.MITM_HSDR_WARN.format(warning=w))
        if not self.ranks:
            print(text.MITM_HSDR_NO_RANKS)

    def response(self, flow) -> None:  # mitmproxy hook
        if _HOST not in flow.request.host:
            return
        ct = flow.response.headers.get("content-type", "")
        if not ct.startswith("application/grpc+proto-enc"):
            return
        path = flow.request.path
        try:
            if path.endswith(_PROFILE_DETAIL):
                if not opt("hoshidori"):
                    return
                ranks = hoshidori.parse_character_ranks(
                    proto.decode(api.decode_response(flow.response.raw_content or b""))
                )
                # only your own profile: someone else's would have ranks for a different account
                if not ranks or ranks == self.ranks or not self._is_own(flow):
                    return
                self.ranks = ranks
                if self.ud is not None:
                    self._write_hoshidori(first=False)
                return
            if self.done or not (path.endswith("User/Get") or path.endswith("Home/Login")):
                return
            ud = self._userdata_from(path, flow.response.raw_content or b"")
            if not ud:
                return
            obj = profile.build(ud)
            counts = profile.summary(obj)
            if not counts["cards"] and not counts["songs"]:
                return  # pre-login/empty snapshot; keep waiting
            self.ud = ud
            self.done = True
            if not (opt("hoshidori") and opt("no_hdbest")):
                dest = profile.write(obj, ctx_out())
                print(
                    text.MITM_CAPTURED.format(
                        cards=counts["cards"], songs=counts["songs"], dest=dest
                    )
                )
            if opt("hoshidori"):
                self._write_hoshidori(first=True)
        except Exception as e:
            print(text.MITM_DECODE_FAILED.format(path=path, error=e))

    def _is_own(self, flow) -> bool:
        """The profile request names whose profile it is; before the snapshot we can't tell yet,
        so accept it (the snapshot normally comes first)."""
        if self.ud is None:
            return True
        try:
            req = proto.decode(api.decode_response(flow.request.raw_content or b""))
            asked = proto.get(
                req, hoshidori._field("ProfileGetUserProfileDetailRequest", "public_user_id", 1)
            )
            if isinstance(asked, (bytes, bytearray)):
                asked = asked.decode("utf-8", "replace")
            return not asked or asked == hoshidori.public_user_id(self.ud)
        except Exception:
            return True


def opt(name: str):
    try:
        from mitmproxy import ctx

        return getattr(ctx.options, name)
    except Exception:
        return None


def ctx_out() -> str:
    return opt("hdbest_out") or os.getcwd()


addons = [HdbestCapture()]
