"""What a sieve answer means, with no network anywhere near it.

`sieve_run` opens the sockets; this is the half that decides. Every judgment the
integration makes is here, so each one can be pinned directly: the request that
gets built, the three statuses that are accepted (and the rest that are errors),
the backoff, the follow-up turn check, the conformance readings, the error
mapping, and the device-login decisions that can waste a login attempt.
"""

from __future__ import annotations

import pytest

from app import sieve as S

BASE = "https://scrape.usesieve.com"


# -- request building --------------------------------------------------------


def test_a_minimal_request_is_minimal():
    body = S.scrape_body("Extract the text and author of each quote")
    assert body == {
        "instruction": "Extract the text and author of each quote",
        "compliance_mode": "regular",
    }


def test_only_what_is_given_is_sent():
    body = S.scrape_body(
        "Extract quotes",
        target_urls=["https://quotes.toscrape.com"],
        fields=["text", "author"],
        table_shape="long",
    )
    assert body["target_urls"] == ["https://quotes.toscrape.com"]
    assert body["fields"] == ["text", "author"]
    assert body["table_shape"] == "long"
    assert "schema" not in body and "output_schema" not in body


def test_regular_is_the_default_policy_and_the_choice_is_kept():
    assert S.scrape_body("x")["compliance_mode"] == "regular"
    assert S.scrape_body("x", compliance_mode="yolo")["compliance_mode"] == "yolo"


def test_urls_are_deduped_and_blank_ones_dropped():
    body = S.scrape_body("x", target_urls=["https://a.test", " ", "https://a.test"])
    assert body["target_urls"] == ["https://a.test"]


def test_a_good_request_validates_clean():
    body = S.scrape_body("x", target_urls=["https://quotes.toscrape.com"])
    assert S.validate_scrape_request(body) == []


def test_validation_names_every_problem_at_once():
    problems = S.validate_scrape_request(
        {"instruction": "", "compliance_mode": "wild", "table_shape": "round"}
    )
    joined = " ".join(problems)
    assert "instruction" in joined
    assert "compliance_mode" in joined
    assert "table_shape" in joined


def test_a_target_url_must_be_a_public_http_page():
    assert S.validate_scrape_request(
        S.scrape_body("x", target_urls=["file:///etc/passwd"])
    )
    assert S.validate_scrape_request(
        S.scrape_body("x", target_urls=["127.0.0.1:8765"])
    )


def test_an_output_schema_over_the_ceiling_is_refused():
    oversized = {"type": "object", "description": "x" * (S.OUTPUT_SCHEMA_MAX_BYTES + 10)}
    problems = S.validate_scrape_request(S.scrape_body("x", output_schema=oversized))
    assert problems and "32" in problems[0]


# -- status handling ---------------------------------------------------------


@pytest.mark.parametrize("status", ["running", "done", "refused"])
def test_the_three_known_statuses_are_accepted(status):
    assert S.run_status({"status": status}) == status


def test_a_status_is_read_case_insensitively():
    assert S.run_status({"status": " Done "}) == "done"


@pytest.mark.parametrize("payload", [{"status": "weird"}, {"status": ""}, {}, None])
def test_anything_that_is_not_a_status_is_an_error(payload):
    with pytest.raises(S.SieveError):
        S.run_status(payload)


def test_a_refusal_carries_its_code():
    assert S.refusal_code({"refusal": {"code": "quota"}}) == "quota"
    assert S.refusal_code({"status": "refused"}) is None
    assert S.refusal_code(None) is None


# -- backoff -----------------------------------------------------------------


def test_polling_starts_at_five_and_settles_at_thirty():
    seen = []
    delay = S.POLL_START
    for _ in range(6):
        delay = S.next_poll_delay(delay)
        seen.append(delay)
    assert seen == [10.0, 20.0, 30.0, 30.0, 30.0, 30.0]


def test_a_missing_previous_delay_still_backs_off_from_five():
    assert S.next_poll_delay(None) == 10.0
    assert S.next_poll_delay(0) == 10.0


# -- follow-up turns ---------------------------------------------------------


def test_a_turn_counts_as_advanced_only_when_it_is_newer():
    assert S.turn_advanced(2, 3)
    assert not S.turn_advanced(3, 3)
    assert not S.turn_advanced(3, 2)


def test_done_before_the_recorded_turn_is_not_the_answer():
    """"done" flips the moment a follow-up is accepted; the turn is the answer."""
    assert not S.ready_for_answer({"status": "done", "turns": 3}, 4)
    assert S.ready_for_answer({"status": "done", "turns": 4}, 4)
    assert not S.ready_for_answer({"status": "running", "turns": 9}, 4)
    assert S.ready_for_answer({"status": "done", "turns": 1}, None)


def test_turns_are_read_defensively():
    assert S.turns_of({"turns": "3"}) == 3
    assert S.turns_of({}) == 0
    assert S.turns_of(None) == 0


# -- conformance -------------------------------------------------------------


def test_only_a_pass_is_clean_data():
    assert S.conformance_is_clean({"schema_conformance": {"status": "pass"}})
    for status in ("partial", "fail", "not_checkable", "no_artifact"):
        assert not S.conformance_is_clean({"schema_conformance": {"status": status}})


def test_no_conformance_block_is_not_a_pass():
    assert not S.conformance_is_clean({})
    assert S.conformance_of({}) is None


def test_a_fail_says_it_is_not_conforming_rather_than_only_that_it_failed():
    assert "non-conforming" in S.conformance_note(S.CONFORMANCE_FAIL)
    # and it is reportable (we show it) even though it is not clean
    report = S.conformance_of({"schema_conformance": {"status": "fail"}})
    assert report is not None and report["clean"] is False


# -- files -------------------------------------------------------------------


def test_files_keep_only_the_ones_that_name_a_url():
    files = S.files_of(
        {
            "files": [
                {"name": "quotes.csv", "url": "/files/q.csv", "size": 10, "ext": "csv"},
                {"name": "broken"},
                "not-an-object",
            ]
        }
    )
    assert len(files) == 1
    assert files[0]["name"] == "quotes.csv"
    assert files[0]["url"] == "/files/q.csv"


def test_a_relative_file_url_is_prefixed_with_the_base():
    assert (
        S.absolute_file_url("/files/q.csv", BASE) == f"{BASE}/files/q.csv"
    )
    assert (
        S.absolute_file_url("files/q.csv", BASE + "/") == f"{BASE}/files/q.csv"
    )
    assert S.absolute_file_url(f"{BASE}/files/q.csv", BASE) == f"{BASE}/files/q.csv"


def test_a_file_can_only_be_fetched_from_the_sieve_origin():
    assert S.is_safe_file_url("/files/q.csv", BASE)
    assert S.is_safe_file_url(f"{BASE}/files/q.csv", BASE)
    assert not S.is_safe_file_url("https://evil.example/q.csv", BASE)
    assert not S.is_safe_file_url("//evil.example/q.csv", BASE)
    assert not S.is_safe_file_url("javascript:alert(1)", BASE)
    assert not S.is_safe_file_url("", BASE)


# -- error mapping -----------------------------------------------------------


@pytest.mark.parametrize(
    "status,kind,retryable",
    [
        (400, "bad_request", False),
        (401, "unauthorized", False),
        (402, "out_of_credits", False),
        (404, "not_found", False),
        (409, "conflict", True),
        (429, "rate_limited", True),
        (500, "server_error", True),
        (503, "server_error", True),
    ],
)
def test_an_http_error_maps_to_what_to_do_about_it(status, kind, retryable):
    failure = S.classify_error(status, {"error": "why"})
    assert failure.kind == kind
    assert failure.retryable is retryable
    assert failure.status == status


def test_429_quotes_the_wait_it_was_given():
    failure = S.classify_error(429, {}, retry_after=12)
    assert failure.retry_after == 12
    assert "12s" in failure.message


def test_the_actionable_ones_say_what_to_look_at():
    assert S.classify_error(400, {}).kind == "bad_request"
    assert "key" in S.classify_error(401, {}).message.lower()
    assert "credits" in S.classify_error(402, {}).message.lower()
    assert "turn" in S.classify_error(409, {}).message.lower()


# -- device login ------------------------------------------------------------


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, {"api_key": "dc_sk_x"}, "approved"),
        (400, {"error": "authorization_pending"}, "pending"),
        (400, {"error": "slow_down"}, "slow_down"),
        (400, {"error": "access_denied"}, "denied"),
        (400, {"error": "expired_token"}, "expired"),
        (400, {"error": "something_else"}, "error"),
        (500, {"error": "boom"}, "error"),
    ],
)
def test_device_poll_answers_are_read(status, body, expected):
    assert S.device_action(status, body) == expected
