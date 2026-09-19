"""Отпечаток должен зависеть от набора юзеров и ни от чего больше.

Панель считает такой же по своей БД (``app/marznode/users_digest.py`` в
репозитории панели) и сравнивает. Если формат разойдётся, сверка начнёт
кричать о расхождении на каждой ноде — поэтому фикстура здесь и там одна.
"""

from marznode.utils.users_digest import users_digest

# Та же пара (набор, отпечаток) проверяется на стороне панели.
FIXTURE = [(1, ["vless-tcp", "vless-reality"]), (2, ["vless-tcp"]), (10, [])]
FIXTURE_DIGEST = users_digest(FIXTURE)


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
