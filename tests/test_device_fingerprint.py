"""Compatibility tests for :mod:`marznode.utils.device_fingerprint`.

The golden vectors below MUST remain identical across marznode and
Marzneshin.  If you change an algorithm, update *both* repositories and
regenerate the expected hashes using ``build_device_fingerprints_all``.
"""

from __future__ import annotations

import pytest

from marznode.utils.device_fingerprint import (
    DEFAULT_FINGERPRINT_VERSION,
    SUPPORTED_FINGERPRINT_VERSIONS,
    build_device_fingerprint,
    build_device_fingerprints_all,
    extract_device_info_from_meta,
    is_device_allowed,
    normalize_client_name,
)


# (inputs, {version: expected_hash})
GOLDEN_VECTORS: list[tuple[dict, dict[int, str]]] = [
    (
        dict(
            user_id=1,
            client_name="v2rayNG",
            tls_fingerprint="abc",
            os_guess="android",
            user_agent="v2rayNG/1.8.5",
        ),
        {
            1: "2e740ba0f613e5344768ff8baa46db61cee0a3d633e0320d249b191fae3398e3",
            2: "2e6e2cc50a5a3cbb1bb40fd5825c4692c0406d624cd6014741d62f1f921afcbb",
        },
    ),
    (
        dict(user_id=42),
        {
            1: "db07614276b63dfec17c6b143d22115430c7f4677eeec887a138623e89927b61",
            2: "fd91a9cca27b01de6cbc8c1cedf12272e8474780ad9e6a3125b393d90c3dd253",
        },
    ),
]


class TestGoldenVectors:
    """Hard-coded hashes to prevent silent drift of the algorithm."""

    @pytest.mark.parametrize("inputs,expected", GOLDEN_VECTORS)
    def test_v1_matches_golden(self, inputs, expected):
        fingerprint, version = build_device_fingerprint(**inputs, version=1)
        assert version == 1
        assert fingerprint == expected[1]

    @pytest.mark.parametrize("inputs,expected", GOLDEN_VECTORS)
    def test_v2_matches_golden(self, inputs, expected):
        fingerprint, version = build_device_fingerprint(**inputs, version=2)
        assert version == 2
        assert fingerprint == expected[2]

    @pytest.mark.parametrize("inputs,expected", GOLDEN_VECTORS)
    def test_all_versions_match_golden(self, inputs, expected):
        assert build_device_fingerprints_all(**inputs) == expected


class TestDefaults:
    def test_default_version_is_v2(self):
        _, version = build_device_fingerprint(user_id=1)
        assert version == DEFAULT_FINGERPRINT_VERSION == 2

    def test_supported_versions_tuple(self):
        assert set(SUPPORTED_FINGERPRINT_VERSIONS) == {1, 2}

    def test_unknown_version_raises(self):
        with pytest.raises(ValueError):
            build_device_fingerprint(user_id=1, version=99)


class TestNormalization:
    def test_case_insensitive_client_name_gives_same_v2(self):
        a, _ = build_device_fingerprint(user_id=7, client_name="v2rayNG", version=2)
        b, _ = build_device_fingerprint(user_id=7, client_name="V2RAYNG", version=2)
        assert a == b

    def test_whitespace_in_tls_stripped_v2(self):
        a, _ = build_device_fingerprint(user_id=1, tls_fingerprint="abc", version=2)
        b, _ = build_device_fingerprint(user_id=1, tls_fingerprint="  ABC  ", version=2)
        assert a == b

    def test_normalize_client_name_known_alias(self):
        assert normalize_client_name("v2rayng") == "v2rayNG"
        assert normalize_client_name("  SHADOWROCKET  ") == "Shadowrocket"

    def test_normalize_client_name_empty(self):
        assert normalize_client_name(None) is None
        assert normalize_client_name("") is None


class TestSeparatorCollisionFix:
    """v1 is vulnerable to ``|`` collisions; v2 must not be."""

    def test_v1_collides_on_pipe(self):
        a, _ = build_device_fingerprint(
            user_id=100, client_name="a", tls_fingerprint="b|c", version=1
        )
        b, _ = build_device_fingerprint(
            user_id=100, client_name="a|b", tls_fingerprint="c", version=1
        )
        assert a == b, "v1 collision is expected (documented legacy behaviour)"

    def test_v2_does_not_collide_on_pipe(self):
        a, _ = build_device_fingerprint(
            user_id=100, client_name="a", tls_fingerprint="b|c", version=2
        )
        b, _ = build_device_fingerprint(
            user_id=100, client_name="a|b", tls_fingerprint="c", version=2
        )
        assert a != b


class TestUnicodeSafety:
    def test_broken_surrogate_does_not_raise(self):
        fingerprint, _ = build_device_fingerprint(
            user_id=1, user_agent="bad\udcff surrogate"
        )
        assert len(fingerprint) == 64

    def test_broken_surrogate_both_versions(self):
        result = build_device_fingerprints_all(
            user_id=1, user_agent="bad\udcff surrogate"
        )
        assert set(result) == {1, 2}
        for fp in result.values():
            assert len(fp) == 64


class TestIsDeviceAllowed:
    def test_enforcement_disabled(self):
        allowed, reason = is_device_allowed(
            "abc", ["xyz"], device_limit=1, enforce=False
        )
        assert allowed is True
        assert reason == "enforcement disabled"

    def test_no_limit(self):
        allowed, reason = is_device_allowed("abc", [], device_limit=None)
        assert allowed is True
        assert reason == "no device limit set"

    def test_limit_zero_blocks(self):
        allowed, reason = is_device_allowed("abc", [], device_limit=0)
        assert allowed is False
        assert "not allowed" in reason

    def test_match_in_allowed_list(self):
        allowed, _ = is_device_allowed("abc", ["abc", "def"], device_limit=2)
        assert allowed is True

    def test_accepts_iterable_of_fingerprints(self):
        """Typical dual-version check: v1 hash matches even though v2 does not."""
        allowed, reason = is_device_allowed(
            fingerprints=("new_v2_hash", "legacy_v1_hash"),
            allowed_fingerprints=["legacy_v1_hash"],
            device_limit=1,
        )
        assert allowed is True
        assert reason == "fingerprint in allowed list"

    def test_under_limit_admits_new_device(self):
        allowed, reason = is_device_allowed("new", ["a"], device_limit=2)
        assert allowed is True
        assert "under device limit" in reason

    def test_over_limit_blocks_unknown(self):
        allowed, reason = is_device_allowed("new", ["a", "b"], device_limit=2)
        assert allowed is False
        assert "exceeded" in reason


class TestExtractDeviceInfoFromMeta:
    def test_all_fields_present(self):
        info = extract_device_info_from_meta(
            {
                "client_name": "v2rayNG",
                "user_agent": "v2rayNG/1.8",
                "tls_fingerprint": "chrome",
                "os_guess": "android",
                "protocol": "vless",
                "remote_ip": "1.2.3.4",
                "unrelated": "ignored",
            }
        )
        assert info == {
            "client_name": "v2rayNG",
            "user_agent": "v2rayNG/1.8",
            "tls_fingerprint": "chrome",
            "os_guess": "android",
            "protocol": "vless",
            "remote_ip": "1.2.3.4",
        }

    def test_missing_fields_become_empty_strings(self):
        info = extract_device_info_from_meta({})
        assert info == {
            "client_name": "",
            "user_agent": "",
            "tls_fingerprint": "",
            "os_guess": "",
            "protocol": "",
            "remote_ip": "",
        }

    def test_non_string_coerced(self):
        info = extract_device_info_from_meta({"remote_ip": 12345})
        assert info["remote_ip"] == "12345"
