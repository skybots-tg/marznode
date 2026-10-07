"""Отпечаток должен зависеть от набора юзеров и ни от чего больше.

Панель считает такой же по своей БД (``app/marznode/users_digest.py`` в
репозитории панели) и сравнивает. Если формат разойдётся, сверка начнёт
кричать о расхождении на каждой ноде — поэтому фикстура здесь и там одна.
"""

from marznode.utils.users_digest import keyed_users_digest, users_digest

# Та же пара (набор, отпечаток) проверяется на стороне панели.
FIXTURE = [(1, ["vless-tcp", "vless-reality"]), (2, ["vless-tcp"]), (10, [])]
FIXTURE_DIGEST = users_digest(FIXTURE)
# Прибиты гвоздями: в tests/test_users_digest.py панели те же строки.
CROSS_REPO_DIGEST = (
    "4cd89b5a70228fae5b01b72d7b22bd21f38b0d19e1e06174e07b66e96b657708"
)
KEYED_FIXTURE = [
    (1, "key1", ["vless-tcp", "vless-reality"]),
    (2, "key2", ["vless-tcp"]),
    (10, "key10", []),
]
CROSS_REPO_KEYED_DIGEST = (
    "3877a9de2e4ec31f782f636afaede69dde0d0ab876042d13dc6cfc18c14c5fc1"
)


def test_the_format_matches_the_panel_side():
    assert FIXTURE_DIGEST == CROSS_REPO_DIGEST
    assert keyed_users_digest(KEYED_FIXTURE) == CROSS_REPO_KEYED_DIGEST


def test_keyed_digest_sees_a_changed_key():
    rekeyed = [(1, "key1-new", KEYED_FIXTURE[0][2]), *KEYED_FIXTURE[1:]]
    assert keyed_users_digest(rekeyed) != CROSS_REPO_KEYED_DIGEST


def test_keyed_digest_ignores_order_like_the_plain_one():
    shuffled = [
        (10, "key10", []),
        (2, "key2", ["vless-tcp", "vless-tcp"]),
        (1, "key1", ["vless-reality", "vless-tcp"]),
    ]
    assert keyed_users_digest(shuffled) == CROSS_REPO_KEYED_DIGEST


def test_order_of_users_and_tags_does_not_matter():
    shuffled = [(2, ["vless-tcp"]), (10, []), (1, ["vless-reality", "vless-tcp"])]
    assert users_digest(shuffled) == FIXTURE_DIGEST


def test_a_repeated_tag_is_the_same_set():
    doubled = [
        (1, ["vless-tcp", "vless-reality", "vless-tcp"]),
        (2, ["vless-tcp"]),
        (10, []),
    ]
    assert users_digest(doubled) == FIXTURE_DIGEST


def test_losing_a_user_changes_it():
    assert users_digest(FIXTURE[:-1]) != FIXTURE_DIGEST


def test_losing_an_inbound_changes_it():
    without = [(1, ["vless-tcp"]), (2, ["vless-tcp"]), (10, [])]
    assert users_digest(without) != FIXTURE_DIGEST


def test_an_empty_fleet_is_stable():
    assert users_digest([]) == users_digest(iter([]))


def test_the_inline_fallback_in_service_py_agrees():
    """service.py дублирует формат для нод, где смонтирован только он.

    Копия существует потому, что обычный импорт там роняет marznode вместе с
    xray; разойтись с оригиналом она не имеет права.
    """
    import marznode.service.service as svc

    assert svc.users_digest(FIXTURE) == FIXTURE_DIGEST
    assert svc.keyed_users_digest(KEYED_FIXTURE) == CROSS_REPO_KEYED_DIGEST


def test_the_fallback_itself_agrees(monkeypatch):
    """Сама копия, а не импортированный оригинал: грузим service.py заново
    так, будто marznode/utils/users_digest.py в образе нет."""
    import importlib.util
    import sys

    import marznode.service.service as svc

    monkeypatch.setitem(sys.modules, "marznode.utils.users_digest", None)
    spec = importlib.util.spec_from_file_location(
        "marznode.service._fallback_probe", svc.__file__
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    assert probe.users_digest.__module__ == "marznode.service._fallback_probe"
    assert probe.users_digest(FIXTURE) == CROSS_REPO_DIGEST
    assert probe.keyed_users_digest(KEYED_FIXTURE) == CROSS_REPO_KEYED_DIGEST
