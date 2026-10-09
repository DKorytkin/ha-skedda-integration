"""Free slots beside ours, and what can be done about them. No HA, no HTTP."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.skedda_scheduler.core.offers import Offer, OfferKind, neighbour_offers
from custom_components.skedda_scheduler.core.provider import Booking

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 9, 9, tzinfo=KYIV)  # Friday morning
TUESDAY = 13


def at(day: int, hour: int) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=KYIV)


def booking(
    day: int, hour: int, *, space: str = "court-1", mine: bool = True, booking_id: str = ""
) -> Booking:
    return Booking(
        id=booking_id or f"b-{day}-{hour}-{space}",
        space_ids=(space,),
        start=at(day, hour),
        end=at(day, hour) + timedelta(hours=1),
        title="",
        is_mine=mine,
    )


def offers(
    ours: dict[str, list[Booking]], others: list[Booking] = (), **kwargs: Any
) -> list[Offer]:  # type: ignore[assignment]
    everyone = [b for bookings in ours.values() for b in bookings] + list(others)
    settings: dict[str, Any] = {"quota_minutes": 60, "now": NOW}
    return neighbour_offers(ours, everyone, **{**settings, **kwargs})


def test_a_lone_booking_offers_the_hour_before_and_the_hour_after() -> None:
    """The example from the request: Tuesday 19:00, so 18:00 and 20:00."""
    found = offers({"acc-a": [booking(TUESDAY, 19)], "acc-b": []})

    assert [(o.kind, o.start, o.account_id) for o in found] == [
        (OfferKind.TAKE, at(TUESDAY, 18), "acc-b"),
        (OfferKind.TAKE, at(TUESDAY, 20), "acc-b"),
    ]
    assert all(o.booking_id == f"b-{TUESDAY}-19-court-1" for o in found)
    assert all(o.space_id == "court-1" for o in found)
    assert found[0].end == at(TUESDAY, 19)


def test_a_neighbour_somebody_holds_is_not_offered() -> None:
    found = offers(
        {"acc-a": [booking(TUESDAY, 19)], "acc-b": []}, [booking(TUESDAY, 20, mine=False)]
    )

    assert [o.start for o in found] == [at(TUESDAY, 18)]


def test_a_booking_on_another_court_does_not_hide_a_neighbour() -> None:
    found = offers(
        {"acc-a": [booking(TUESDAY, 19)], "acc-b": []},
        [booking(TUESDAY, 20, space="court-2", mine=False)],
    )

    assert [o.start for o in found] == [at(TUESDAY, 18), at(TUESDAY, 20)]


def test_a_day_with_two_of_ours_is_full() -> None:
    found = offers({"acc-a": [booking(TUESDAY, 19)], "acc-b": [booking(TUESDAY, 20)], "c": []})

    assert found == []


def test_two_of_ours_apart_still_fill_the_day() -> None:
    found = offers({"acc-a": [booking(TUESDAY, 17)], "acc-b": [booking(TUESDAY, 20)], "c": []})

    assert found == []


def test_without_quota_the_booking_can_be_moved_instead() -> None:
    """Nobody can pay for another hour, so swap the one we have."""
    found = offers({"acc-a": [booking(TUESDAY, 19)]})

    assert [(o.kind, o.start, o.account_id) for o in found] == [
        (OfferKind.MOVE, at(TUESDAY, 18), "acc-a"),
        (OfferKind.MOVE, at(TUESDAY, 20), "acc-a"),
    ]


def test_a_week_a_job_is_aiming_at_is_not_spent_on_a_neighbour() -> None:
    from custom_components.skedda_scheduler.core.watch import week_of

    found = offers(
        {"acc-a": [booking(TUESDAY, 19)], "acc-b": []},
        reserved={"acc-b": {week_of(at(TUESDAY, 19))}},
    )

    assert {o.kind for o in found} == {OfferKind.MOVE}


def test_quota_is_counted_in_the_week_of_the_slot() -> None:
    """acc-b spent this week's hour; next Tuesday it is free again."""
    ours = {"acc-a": [booking(TUESDAY, 19)], "acc-b": [booking(10, 19)]}

    found = offers(ours)

    tuesday = [o for o in found if o.start.day == TUESDAY]
    assert {(o.kind, o.account_id) for o in tuesday} == {(OfferKind.TAKE, "acc-b")}


def test_an_unlimited_venue_lets_anyone_take_it() -> None:
    found = offers({"acc-a": [booking(TUESDAY, 19)]}, quota_minutes=None)

    assert {(o.kind, o.account_id) for o in found} == {(OfferKind.TAKE, "acc-a")}


def test_an_hour_the_venue_is_shut_is_not_offered() -> None:
    def shuts_at_eight(space: str, start: datetime, end: datetime) -> bool:
        return end.hour <= 20 and end.date() == start.date()

    found = offers({"acc-a": [booking(TUESDAY, 19)], "acc-b": []}, is_open=shuts_at_eight)

    assert [o.start for o in found] == [at(TUESDAY, 18)]


def test_an_hour_too_soon_for_the_venue_is_not_offered() -> None:
    """At 15:30 with three hours' notice, 18:00 can no longer be booked."""
    found = offers(
        {"acc-a": [booking(9, 19)], "acc-b": []},
        now=at(9, 15) + timedelta(minutes=30),
        min_lead_minutes=180,
    )

    assert [o.start for o in found] == [at(9, 20)]


def test_a_booking_already_under_way_offers_nothing() -> None:
    found = offers({"acc-a": [booking(9, 8)], "acc-b": []})

    assert found == []


def test_offers_are_in_time_order_across_days() -> None:
    found = offers({"acc-a": [booking(15, 19)], "acc-b": [booking(TUESDAY, 19)], "c": []})

    starts = [o.start for o in found]
    assert starts == sorted(starts)
    assert {o.start.day for o in found} == {TUESDAY, 15}


def test_a_booking_without_a_court_offers_nothing() -> None:
    courtless = Booking(
        id="x", space_ids=(), start=at(TUESDAY, 19), end=at(TUESDAY, 20), title="", is_mine=True
    )

    assert offers({"acc-a": [courtless], "acc-b": []}) == []
