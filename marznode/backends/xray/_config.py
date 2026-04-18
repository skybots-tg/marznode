import json
import logging
from collections import defaultdict

import commentjson

from marznode.config import XRAY_EXECUTABLE_PATH, XRAY_VLESS_REALITY_FLOW, DEBUG
from ._utils import get_x25519_with_error
from ...models import Inbound
from ...storage import BaseStorage

logger = logging.getLogger(__name__)

transport_map = defaultdict(
    lambda: "tcp",
    {
        "tcp": "tcp",
        "raw": "tcp",
        "splithttp": "splithttp",
        "xhttp": "splithttp",
        "grpc": "grpc",
        "kcp": "kcp",
        "mkcp": "kcp",
        "h2": "http",
        "h3": "http",
        "http": "http",
        "ws": "ws",
        "websocket": "ws",
        "httpupgrade": "httpupgrade",
        "quic": "quic",
    },
)

forced_policies = {
  "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
  "system": {
    "statsInboundDownlink": False,
    "statsInboundUplink": False,
    "statsOutboundDownlink": True,
    "statsOutboundUplink": True
  }
}

API_INBOUND_TAG = "API_INBOUND"
API_OUTBOUND_TAG = "API"


class XrayConfigValidationError(ValueError):
    """Raised when xray config fails validation before being passed to xray."""


def merge_dicts(a, b): # B overrides A dict
    for key, value in b.items():
        if isinstance(value, dict) and key in a and isinstance(a[key], dict):
            merge_dicts(a[key], value)
        else:
            a[key] = value
    return a

class XrayConfig(dict):
    def __init__(
        self,
        config: str,
        api_host: str = "127.0.0.1",
        api_port: int = 8080,
    ):
        try:
            # considering string as json
            config = commentjson.loads(config)
        except (json.JSONDecodeError, ValueError):
            # considering string as file path
            with open(config) as file:
                config = commentjson.loads(file.read())

        self.api_host = api_host
        self.api_port = api_port

        super().__init__(config)

        self.inbounds = []
        self.inbounds_by_tag = {}
        # self._fallbacks_inbound = self.get_inbound(XRAY_FALLBACKS_INBOUND_TAG)
        self._resolve_inbounds()

        self._apply_api()
        self._validate_routing()

    def _apply_api(self):
        """Idempotently inject the API service, inbound, and routing rule.

        Safe to call on configs that already contain a manually-defined
        API_INBOUND or API outbound tag — duplicates are not introduced.
        """
        self["api"] = {
            "services": ["HandlerService", "StatsService", "LoggerService"],
            "tag": API_OUTBOUND_TAG,
        }
        self["stats"] = {}
        if self.get("policy"):
            self["policy"] = merge_dicts(self.get("policy"), forced_policies)
        else:
            self["policy"] = forced_policies

        if "inbounds" not in self or not isinstance(self.get("inbounds"), list):
            self["inbounds"] = []

        existing_api_inbound = next(
            (i for i in self["inbounds"] if i.get("tag") == API_INBOUND_TAG),
            None,
        )
        api_inbound = {
            "listen": self.api_host,
            "port": self.api_port,
            "protocol": "dokodemo-door",
            "settings": {"address": self.api_host},
            "tag": API_INBOUND_TAG,
        }
        if existing_api_inbound is None:
            self["inbounds"].insert(0, api_inbound)
        else:
            # Reuse the slot but enforce our host/port/protocol so the API
            # client can always reach it on the port we picked.
            existing_api_inbound.update(api_inbound)
            logger.debug(
                "API_INBOUND already present in config; reusing slot and "
                "forcing port=%s host=%s",
                self.api_port,
                self.api_host,
            )

        if "routing" not in self or not isinstance(self.get("routing"), dict):
            self["routing"] = {"rules": []}
        if not isinstance(self["routing"].get("rules"), list):
            self["routing"]["rules"] = []

        api_rule = {
            "inboundTag": [API_INBOUND_TAG],
            "outboundTag": API_OUTBOUND_TAG,
            "type": "field",
        }

        def _is_api_rule(rule: dict) -> bool:
            if not isinstance(rule, dict):
                return False
            inbound_tags = rule.get("inboundTag") or []
            if isinstance(inbound_tags, str):
                inbound_tags = [inbound_tags]
            return (
                API_INBOUND_TAG in inbound_tags
                and rule.get("outboundTag") == API_OUTBOUND_TAG
            )

        rules = self["routing"]["rules"]
        existing_api_rule_idx = next(
            (idx for idx, r in enumerate(rules) if _is_api_rule(r)),
            None,
        )
        if existing_api_rule_idx is None:
            rules.insert(0, api_rule)
        elif existing_api_rule_idx != 0:
            # API rule must be evaluated first or other catch-all rules
            # could swallow API traffic and break stats/handler calls.
            rules.insert(0, rules.pop(existing_api_rule_idx))
            logger.debug("Moved existing API routing rule to index 0")

    def _validate_routing(self):
        """Pre-flight checks on routing.rules before xray sees the config.

        Catches the common foot-guns that lead to silent breakage:
          * routing/rules being the wrong shape
          * the API rule not being first (must be guaranteed by _apply_api)
          * a user-defined catch-all rule placed BEFORE the API rule that
            would swallow traffic destined for API_INBOUND
          * a catch-all rule pointing at the `block` outbound that has no
            scoping fields (effectively kills all traffic)
        """
        routing = self.get("routing")
        if not isinstance(routing, dict):
            raise XrayConfigValidationError("routing must be an object")
        rules = routing.get("rules")
        if not isinstance(rules, list):
            raise XrayConfigValidationError("routing.rules must be a list")

        if not rules:
            return

        # API rule guarantee (defensive — _apply_api already ensures this)
        first = rules[0]
        first_inbound_tags = first.get("inboundTag") or []
        if isinstance(first_inbound_tags, str):
            first_inbound_tags = [first_inbound_tags]
        if (
            API_INBOUND_TAG not in first_inbound_tags
            or first.get("outboundTag") != API_OUTBOUND_TAG
        ):
            raise XrayConfigValidationError(
                "API routing rule must be the first entry in routing.rules"
            )

        scoping_fields = (
            "domain",
            "ip",
            "port",
            "sourcePort",
            "network",
            "source",
            "user",
            "inboundTag",
            "protocol",
            "attrs",
            "domainMatcher",
        )

        for idx, rule in enumerate(rules[1:], start=1):
            if not isinstance(rule, dict):
                raise XrayConfigValidationError(
                    f"routing.rules[{idx}] must be an object"
                )
            has_scope = any(rule.get(f) for f in scoping_fields)
            outbound = rule.get("outboundTag") or rule.get("balancerTag")
            if not has_scope and outbound in {"block", "blocked", "blackhole"}:
                raise XrayConfigValidationError(
                    f"routing.rules[{idx}] is an unscoped catch-all that "
                    f"routes to '{outbound}', which would block ALL traffic"
                )
            inbound_tags = rule.get("inboundTag") or []
            if isinstance(inbound_tags, str):
                inbound_tags = [inbound_tags]
            if API_INBOUND_TAG in inbound_tags and outbound != API_OUTBOUND_TAG:
                raise XrayConfigValidationError(
                    f"routing.rules[{idx}] re-routes API_INBOUND to "
                    f"'{outbound}', which would break the xray API"
                )

    def _resolve_inbounds(self):
        for inbound in self["inbounds"]:
            if (
                inbound.get("protocol", "").lower()
                not in {
                    "vmess",
                    "trojan",
                    "vless",
                    "shadowsocks",
                }
                or "tag" not in inbound
            ):
                continue

            settings = {
                "tag": inbound["tag"],
                "protocol": inbound["protocol"],
                "port": inbound.get("port"),
                "network": "tcp",
                "tls": "none",
                "sni": [],
                "host": [],
                "path": None,
                "header_type": None,
                "flow": None,
                "is_fallback": False,
            }

            # port settings, TODO: fix port and stream settings for fallbacks

            # stream settings
            if stream := inbound.get("streamSettings"):
                net = stream.get("network", "tcp")
                net_settings = stream.get(f"{net}Settings", {})
                security = stream.get("security")
                tls_settings = stream.get(f"{security}Settings")

                settings["network"] = transport_map[net]

                if security == "tls":
                    settings["tls"] = "tls"
                elif security == "reality":
                    settings["fp"] = "chrome"
                    settings["tls"] = "reality"
                    settings["sni"] = tls_settings.get("serverNames", [])
                    if inbound["protocol"] == "vless" and transport_map[net] == "tcp":
                        settings["flow"] = XRAY_VLESS_REALITY_FLOW

                    pvk = tls_settings.get("privateKey")
                    tag = inbound.get("tag", "unknown")

                    x25519, reason = get_x25519_with_error(
                        XRAY_EXECUTABLE_PATH, pvk
                    )
                    if x25519 is None and pvk:
                        # The provided private key is most likely malformed for
                        # this xray version. Fall back to a freshly generated
                        # pair so the panel does not crash on a single bad
                        # inbound — the specific inbound will still be broken
                        # until the admin fixes the config, but the rest of
                        # the backend keeps working.
                        logger.warning(
                            "Failed to derive x25519 public key from privateKey "
                            "of inbound '%s' (%s). Falling back to a generated "
                            "keypair; the advertised public key will not match "
                            "xray's private key until the config is fixed.",
                            tag,
                            reason,
                        )
                        x25519, reason = get_x25519_with_error(
                            XRAY_EXECUTABLE_PATH
                        )

                    if x25519 is None:
                        logger.error(
                            "Failed to generate x25519 keys for inbound '%s': "
                            "%s. Check that Xray is properly installed at %s. "
                            "Reality parameters for this inbound will be left "
                            "empty.",
                            tag,
                            reason,
                            XRAY_EXECUTABLE_PATH,
                        )
                        settings["pbk"] = ""
                    else:
                        settings["pbk"] = x25519["public_key"]

                    settings["sid"] = tls_settings.get("shortIds", [""])[0]

                if net in ["tcp", "raw"]:
                    header = net_settings.get("header", {})
                    request = header.get("request", {})
                    path = request.get("path")
                    host = request.get("headers", {}).get("Host")

                    settings["header_type"] = header.get("type")

                    if path and isinstance(path, list):
                        settings["path"] = path[0]

                    if host and isinstance(host, list):
                        settings["host"] = host

                elif net in ["ws", "websocket", "httpupgrade", "splithttp", "xhttp"]:
                    settings["path"] = net_settings.get("path")
                    settings["host"] = net_settings.get("host")

                elif net == "grpc":
                    settings["path"] = net_settings.get("serviceName")

                elif net in ["kcp", "mkcp"]:
                    settings["path"] = net_settings.get("seed")
                    settings["header_type"] = net_settings.get("header", {}).get("type")

                elif net == "quic":
                    settings["host"] = net_settings.get("security")
                    settings["path"] = net_settings.get("key")
                    settings["header_type"] = net_settings.get("header", {}).get("type")

                elif net == "http":
                    settings["path"] = net_settings.get("path")
                    settings["host"] = net_settings.get("host")

            if inbound["protocol"] == "shadowsocks":
                settings["network"] = None

            self.inbounds.append(settings)
            self.inbounds_by_tag[inbound["tag"]] = settings

    def register_inbounds(self, storage: BaseStorage):
        for inbound in self.list_inbounds():
            storage.register_inbound(inbound)

    def list_inbounds(self) -> list[Inbound]:
        return [
            Inbound(tag=i["tag"], protocol=i["protocol"], config=i)
            for i in self.inbounds_by_tag.values()
        ]

    def to_json(self, **json_kwargs):
        if DEBUG:
            with open('xray_config_debug.json', 'w') as f:
                f.write(json.dumps(self, indent=4))
        return json.dumps(self, **json_kwargs)
