from __future__ import annotations

import pytest
from conftest import OWNER
from conftest import as_caller
from conftest import scope
from without_asgi import HttpScope

from exe_dev_atlas.app import CALLER_EMAIL_HEADER
from exe_dev_atlas.app import is_owner


def test_the_owners_own_address_is_recognised() -> None:
    assert is_owner(as_caller(OWNER), OWNER) is True


@pytest.mark.parametrize(
    "sent",
    [
        pytest.param("OWNER@EXAMPLE.COM", id="uppercase"),
        pytest.param("Owner@Example.Com", id="mixed-case"),
        pytest.param("  owner@example.com  ", id="surrounded-by-whitespace"),
    ],
)
def test_an_address_that_differs_only_in_case_or_padding_is_still_the_owner(sent: str) -> None:
    assert is_owner(as_caller(sent), OWNER) is True


def test_the_header_name_is_matched_case_insensitively() -> None:
    # HTTP/2 lowercases header names and HTTP/1.1 does not, so a match on the exact bytes
    # would authorize over one protocol and not the other.
    assert is_owner(scope((b"X-ExeDev-Email", OWNER.encode())), OWNER) is True


@pytest.mark.parametrize(
    "caller",
    [
        pytest.param("someone@else.com", id="a-different-person"),
        pytest.param("owner@example.com.evil.test", id="the-owner-as-a-prefix"),
        pytest.param("notowner@example.com", id="the-owner-as-a-suffix"),
        pytest.param("owner@example", id="a-truncated-address"),
    ],
)
def test_anybody_else_is_not_the_owner(caller: str) -> None:
    assert is_owner(as_caller(caller), OWNER) is False


class TestADuplicatedHeader:
    """
    The last value wins, because the proxy's is the last one added.

    A proxy that appends rather than replaces leaves whatever the client sent first in the
    list, so reading the first value would let a caller authorize themselves by sending the
    header twice: their own address, then the one the proxy adds behind it.
    """

    def test_the_owner_behind_a_forged_address_is_still_the_owner(self) -> None:
        sent = scope((CALLER_EMAIL_HEADER, b"someone@else.com"), (CALLER_EMAIL_HEADER, OWNER.encode()))

        assert is_owner(sent, OWNER) is True

    def test_a_forged_address_behind_the_proxys_own_is_not_the_owner(self) -> None:
        sent = scope((CALLER_EMAIL_HEADER, OWNER.encode()), (CALLER_EMAIL_HEADER, b"someone@else.com"))

        assert is_owner(sent, OWNER) is False


class TestNonAsciiAddresses:
    def test_an_internationalized_owner_address_is_recognised(self) -> None:
        # Read as latin-1 the header's UTF-8 bytes arrive as `josé`, which matches nobody and
        # locks this owner out of their own box for as long as the address holds.
        owner = "josé@example.com"

        assert is_owner(scope((CALLER_EMAIL_HEADER, owner.encode())), owner) is True

    def test_a_header_that_is_not_valid_utf_8_is_not_the_owner(self) -> None:
        assert is_owner(scope((CALLER_EMAIL_HEADER, b"\xff\xfe@example.com")), OWNER) is False


def test_a_caller_the_proxy_did_not_authenticate_is_not_the_owner() -> None:
    # The proxy omits the header entirely for an unauthenticated caller, which is why the
    # comparison has to fail closed rather than treat absence as a match.
    assert is_owner(scope(), OWNER) is False


def test_an_empty_header_value_is_not_the_owner() -> None:
    assert is_owner(as_caller(""), OWNER) is False


class TestFailingClosed:
    """
    The case that matters most: reflection not answering at startup leaves the owner "".

    An unauthenticated caller also produces "", so a plain equality check would make
    `"" == ""` true and disclose every session name to everyone at exactly the moment this
    process knows least about who anyone is.
    """

    @pytest.mark.parametrize(
        "caller",
        [
            pytest.param(scope(), id="no-header-at-all"),
            pytest.param(as_caller(""), id="an-empty-address"),
            pytest.param(as_caller("   "), id="whitespace"),
            pytest.param(as_caller("someone@else.com"), id="a-different-person"),
            pytest.param(as_caller(OWNER), id="the-address-that-would-otherwise-match"),
        ],
    )
    def test_nobody_is_the_owner_when_reflection_gave_no_owner(self, caller: HttpScope) -> None:
        assert is_owner(caller, "") is False

    def test_an_owner_of_only_whitespace_authorizes_nobody(self) -> None:
        assert is_owner(as_caller("   "), "   ") is False
