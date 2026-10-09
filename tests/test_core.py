"""Tests for the parts that must be right before a phone is ever attached.

The device link is replaced by a recorder, so a full session -- connect,
teleport, route, free roam, stop, restore -- runs end to end and every
coordinate the tool would have pushed over USB is asserted on instead.

Run: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iosloc import errors, session as session_module  # noqa: E402
from iosloc.device import DeviceInfo  # noqa: E402
from iosloc.geo import bearing, destination, distance, interpolate  # noqa: E402
from iosloc.profiles import PROFILES, get_profile, kmh_to_ms  # noqa: E402
from iosloc.route import LoopMode, ManualRunner, RouteRunner, Track  # noqa: E402
from iosloc.session import LocationSession  # noqa: E402

MOSCOW = (55.7558, 37.6176)
PITER = (59.9386, 30.3141)


class FakeLink:
    """Stands in for `DeviceLink`, recording every fix instead of sending it."""

    instances: list["FakeLink"] = []

    def __init__(self, udid=None, allow_tunneld=True, enable_developer_mode=False):
        self.udid = udid or "FAKEUDID"
        self.transport = "userspace-tunnel"
        self.info = DeviceInfo(
            udid=self.udid,
            name="iPhone 11 Pro",
            model="iPhone 11 Pro",
            product_type="iPhone12,3",
            ios_version="17.5",
            connection_type="USB",
        )
        self.is_open = False
        self.sent: list[tuple[float, float]] = []
        self.cleared = 0
        FakeLink.instances.append(self)

    async def open(self):
        self.is_open = True
        return self

    async def close(self):
        self.is_open = False

    #: Number of upcoming set() calls that should fail, as a dropped cable would.
    fail_sends = 0

    async def set(self, latitude, longitude):
        if self.fail_sends > 0:
            self.fail_sends -= 1
            raise ConnectionResetError("device went away")
        self.sent.append((latitude, longitude))

    async def clear(self):
        self.cleared += 1


class GeoTests(unittest.TestCase):
    def test_distance_matches_known_leg(self):
        # Moscow -> St Petersburg is ~634 km great-circle.
        metres = distance(*MOSCOW, *PITER)
        self.assertAlmostEqual(metres / 1000, 634, delta=3)

    def test_destination_round_trips(self):
        start = MOSCOW
        for heading in (0, 45, 123, 270, 359):
            end = destination(*start, heading, 5000)
            self.assertAlmostEqual(distance(*start, *end), 5000, delta=0.5)
            self.assertAlmostEqual(bearing(*start, *end), heading, delta=0.01)

    def test_interpolate_midpoint_is_half_way(self):
        middle = interpolate(*MOSCOW, *PITER, 0.5)
        self.assertAlmostEqual(distance(*MOSCOW, *middle), distance(*middle, *PITER), delta=1.0)

    def test_antimeridian_longitude_stays_in_range(self):
        _, longitude = destination(0.0, 179.9, 90.0, 50_000)
        self.assertTrue(-180.0 <= longitude <= 180.0)


class TrackTests(unittest.TestCase):
    def test_length_is_sum_of_legs(self):
        track = Track([(55.75, 37.61), (55.76, 37.62), (55.77, 37.63)])
        expected = distance(55.75, 37.61, 55.76, 37.62) + distance(55.76, 37.62, 55.77, 37.63)
        self.assertAlmostEqual(track.length, expected, delta=0.01)

    def test_duplicate_points_are_dropped(self):
        track = Track([(55.75, 37.61), (55.75, 37.61), (55.76, 37.62)])
        self.assertEqual(len(track), 2)

    def test_single_point_track_is_degenerate(self):
        track = Track([(55.75, 37.61)])
        self.assertTrue(track.is_degenerate)
        self.assertEqual(track.length, 0.0)

    def test_empty_track_rejected(self):
        with self.assertRaises(ValueError):
            Track([])

    def test_position_at_ends_is_exact(self):
        track = Track([(55.75, 37.61), (55.77, 37.63)])
        start = track.position_at(0.0)
        end = track.position_at(track.length)
        self.assertAlmostEqual(distance(start.latitude, start.longitude, 55.75, 37.61), 0, delta=0.01)
        self.assertAlmostEqual(distance(end.latitude, end.longitude, 55.77, 37.63), 0, delta=0.01)

    def test_position_clamps_past_the_end(self):
        track = Track([(55.75, 37.61), (55.77, 37.63)])
        beyond = track.position_at(track.length * 10)
        self.assertAlmostEqual(distance(beyond.latitude, beyond.longitude, 55.77, 37.63), 0, delta=0.01)

    def test_invalid_coordinate_rejected(self):
        with self.assertRaises(ValueError):
            Track([(91.0, 0.0)])


class RouteRunnerTests(unittest.TestCase):
    def _run(self, runner, dt=1.0, limit=100_000):
        ticks = 0
        while not runner.finished and ticks < limit:
            runner.advance(dt)
            ticks += 1
        return ticks

    def test_once_finishes_at_the_last_point(self):
        track = Track([(55.75, 37.61), (55.76, 37.62)])
        runner = RouteRunner(track=track, speed=5.0, accel=1.0, mode=LoopMode.ONCE)
        self._run(runner)
        self.assertTrue(runner.finished)
        fix = runner.current()
        self.assertAlmostEqual(distance(fix.latitude, fix.longitude, 55.76, 37.62), 0, delta=0.5)

    def test_speed_ramps_up_and_brakes_to_a_stop(self):
        track = Track([(55.75, 37.61), (55.80, 37.61)])
        runner = RouteRunner(track=track, speed=20.0, accel=1.5, mode=LoopMode.ONCE)
        speeds = []
        while not runner.finished:
            speeds.append(runner.advance(1.0).speed)
        self.assertLess(speeds[0], 20.0, "first tick should still be accelerating")
        self.assertAlmostEqual(max(speeds), 20.0, delta=0.6)
        # The braking ramp means the fixes before the end are slower than cruise.
        self.assertLess(speeds[-2], 20.0)

    def test_zero_accel_starts_at_cruise(self):
        track = Track([(55.75, 37.61), (55.80, 37.61)])
        runner = RouteRunner(track=track, speed=12.0, accel=0.0)
        self.assertAlmostEqual(runner.advance(1.0).speed, 12.0, delta=0.01)

    def test_loop_wraps_without_finishing(self):
        track = Track([(55.75, 37.61), (55.7505, 37.61)])
        runner = RouteRunner(track=track, speed=30.0, accel=0.0, mode=LoopMode.LOOP)
        for _ in range(200):
            runner.advance(1.0)
        self.assertFalse(runner.finished)
        self.assertLessEqual(runner.travelled, track.length)

    def test_pingpong_reverses_direction(self):
        track = Track([(55.75, 37.61), (55.7505, 37.61)])
        runner = RouteRunner(track=track, speed=20.0, accel=0.0, mode=LoopMode.PINGPONG)
        seen_reverse = False
        for _ in range(300):
            runner.advance(1.0)
            if runner.direction < 0:
                seen_reverse = True
            self.assertTrue(0.0 <= runner.travelled <= track.length + 1e-6)
        self.assertTrue(seen_reverse)
        self.assertFalse(runner.finished)

    def test_jitter_stays_inside_its_radius(self):
        track = Track([(55.75, 37.61), (55.80, 37.61)])
        runner = RouteRunner(track=track, speed=10.0, accel=0.0, jitter_m=8.0)
        for _ in range(200):
            clean = runner.track.position_at(runner.travelled)
            fix = runner.advance(1.0)
            offset = distance(clean.latitude, clean.longitude, fix.latitude, fix.longitude)
            # advance() moves first, so compare against a one-step window.
            self.assertLess(offset, 8.0 + runner.speed + 1.0)

    def test_eta_matches_distance_over_speed(self):
        track = Track([(55.75, 37.61), (55.80, 37.61)])
        runner = RouteRunner(track=track, speed=10.0, mode=LoopMode.ONCE)
        self.assertAlmostEqual(runner.eta, track.length / 10.0, delta=0.01)

    def test_degenerate_track_finishes_immediately(self):
        runner = RouteRunner(track=Track([(55.75, 37.61)]), speed=5.0)
        fix = runner.advance(1.0)
        self.assertTrue(runner.finished)
        self.assertAlmostEqual(fix.latitude, 55.75, places=6)


class ManualRunnerTests(unittest.TestCase):
    def test_turn_rate_limits_heading_change(self):
        runner = ManualRunner(latitude=55.75, longitude=37.61, heading=0.0, turn_rate=45.0)
        runner.steer(heading=180.0, speed=5.0)
        self.assertAlmostEqual(runner.advance(1.0).heading, 45.0, delta=0.01)
        self.assertAlmostEqual(runner.advance(1.0).heading, 90.0, delta=0.01)

    def test_turn_takes_the_short_way_round(self):
        runner = ManualRunner(latitude=55.75, longitude=37.61, heading=10.0, turn_rate=20.0)
        runner.steer(heading=350.0)
        self.assertAlmostEqual(runner.advance(1.0).heading, 350.0, delta=0.01)

    def test_moves_along_its_heading(self):
        runner = ManualRunner(latitude=55.75, longitude=37.61, heading=90.0, speed=10.0, accel=0.0)
        start = (runner.latitude, runner.longitude)
        fix = runner.advance(10.0)
        self.assertAlmostEqual(distance(*start, fix.latitude, fix.longitude), 100.0, delta=1.0)
        self.assertAlmostEqual(bearing(*start, fix.latitude, fix.longitude), 90.0, delta=0.5)

    def test_zero_speed_holds_position(self):
        runner = ManualRunner(latitude=55.75, longitude=37.61, speed=0.0, jitter_m=0.0)
        fix = runner.advance(60.0)
        self.assertEqual((fix.latitude, fix.longitude), (55.75, 37.61))


class ProfileTests(unittest.TestCase):
    def test_every_profile_is_coherent(self):
        for key, profile in PROFILES.items():
            self.assertEqual(profile.key, key)
            self.assertGreaterEqual(profile.speed, 0.0)
            self.assertGreater(profile.interval, 0.0)
            self.assertGreaterEqual(profile.jitter_m, 0.0)
            self.assertTrue(0.0 <= profile.speed_jitter < 1.0)

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            get_profile("teleporter")

    def test_plane_is_the_fastest_profile(self):
        self.assertEqual(max(PROFILES.values(), key=lambda p: p.speed).key, "plane")


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeLink.instances.clear()
        self._real_link = session_module.DeviceLink
        session_module.DeviceLink = FakeLink
        self.session = LocationSession()
        await self.session.connect()
        self.link = FakeLink.instances[-1]

    async def asyncTearDown(self):
        await self.session.disconnect(restore=True)
        session_module.DeviceLink = self._real_link

    async def test_connect_reports_the_device(self):
        self.assertTrue(self.session.connected)
        self.assertEqual(self.session.device.name, "iPhone 11 Pro")
        self.assertEqual(self.session.state()["transport"], "userspace-tunnel")

    async def test_teleport_sends_the_exact_point(self):
        await self.session.teleport(55.7539, 37.6208, profile="stand")
        self.assertEqual(self.link.sent[0], (55.7539, 37.6208))
        position = self.session.state()["position"]
        self.assertAlmostEqual(position["lat"], 55.7539, places=4)

    async def test_route_pushes_a_moving_sequence(self):
        await self.session.follow(
            points=[(55.7558, 37.6176), (55.7600, 37.6176)],
            profile="city",
            from_current=False,
        )
        await asyncio.sleep(3.2)
        await self.session.stop()
        self.assertGreaterEqual(len(self.link.sent), 2)
        first, last = self.link.sent[0], self.link.sent[-1]
        self.assertGreater(distance(*first, *last), 5.0, "the device should have moved")

    async def test_route_state_reports_progress_and_eta(self):
        await self.session.follow(
            points=[(55.7558, 37.6176), (55.8000, 37.6176)],
            profile="highway",
            from_current=False,
        )
        await asyncio.sleep(1.5)
        route = self.session.state()["route"]
        self.assertIsNotNone(route)
        self.assertGreater(route["length_m"], 4000)
        self.assertIsNotNone(route["eta_s"])
        await self.session.stop()

    async def test_stop_without_restore_holds_the_last_point(self):
        await self.session.teleport(55.75, 37.61)
        await self.session.stop(restore=False)
        self.assertEqual(self.link.cleared, 0)
        self.assertTrue(self.session.state()["override_active"])

    async def test_restore_clears_the_override(self):
        await self.session.teleport(55.75, 37.61)
        await self.session.stop(restore=True)
        self.assertEqual(self.link.cleared, 1)
        self.assertFalse(self.session.state()["override_active"])
        self.assertIsNone(self.session.state()["position"])

    async def test_disconnect_always_restores(self):
        await self.session.teleport(55.75, 37.61)
        await self.session.disconnect(restore=True)
        self.assertEqual(self.link.cleared, 1)
        self.assertFalse(self.session.connected)
        # asyncTearDown disconnects again; that must stay harmless.
        await self.session.connect()
        self.link = FakeLink.instances[-1]

    async def test_steering_moves_in_the_requested_direction(self):
        await self.session.teleport(55.75, 37.61, profile="city")
        await self.session.steer(heading=90.0, speed_kmh=120.0, snap_heading=True)
        # "city" accelerates at 2.2 m/s^2, so the first seconds are deliberately
        # slow -- give the ramp time rather than asserting an instant jump.
        await asyncio.sleep(4.5)
        await self.session.stop()
        start, end = self.link.sent[0], self.link.sent[-1]
        self.assertGreater(distance(*start, *end), 10.0)
        self.assertAlmostEqual(bearing(*start, *end), 90.0, delta=25.0)

    async def test_pause_freezes_the_position(self):
        await self.session.follow(
            points=[(55.7558, 37.6176), (55.8000, 37.6176)],
            profile="highway",
            from_current=False,
        )
        await asyncio.sleep(1.2)
        await self.session.pause()
        frozen = len(self.link.sent)
        await asyncio.sleep(1.6)
        self.assertEqual(len(self.link.sent), frozen, "a paused session must not send fixes")
        await self.session.resume()
        await asyncio.sleep(1.4)
        self.assertGreater(len(self.link.sent), frozen)
        await self.session.stop()

    async def test_unchanged_position_is_not_resent_every_tick(self):
        await self.session.teleport(55.75, 37.61, profile="stand")
        sent_at_start = len(self.link.sent)
        await asyncio.sleep(2.5)
        # "stand" jitters by 2.5 m, which is above the resend threshold, so a
        # few fixes are expected -- just not one per tick of a 2 s interval.
        self.assertLessEqual(len(self.link.sent) - sent_at_start, 3)

    async def test_profile_switch_retunes_a_running_route(self):
        await self.session.follow(
            points=[(55.7558, 37.6176), (55.9000, 37.6176)],
            profile="walk",
            from_current=False,
        )
        await self.session.set_profile("plane")
        route = self.session.state()["route"]
        self.assertAlmostEqual(route["target_speed_kmh"], 900.0, delta=1.0)
        await self.session.stop()

    async def test_journey_switches_profile_between_legs(self):
        """Legs hand over automatically, each with its own profile."""
        legs = [
            {"points": [[55.75000, 37.60000], [55.75007, 37.60000]], "profile": "walk", "note": "a"},
            {"points": [[55.75007, 37.60000], [55.75107, 37.60000]], "profile": "city", "note": "b"},
            {"points": [[55.75107, 37.60000], [55.75114, 37.60000]], "profile": "walk", "note": "c"},
        ]
        summary = await self.session.follow_journey(legs, from_current=False)
        self.assertEqual(len(summary["legs"]), 3)

        seen = []
        for _ in range(400):
            await asyncio.sleep(0.1)
            journey = self.session.state()["journey"]
            if journey is None:
                continue
            step = (journey["leg"], journey["profile"])
            if not seen or seen[-1] != step:
                seen.append(step)
            if journey["finished"]:
                break

        self.assertEqual([s[0] for s in seen], [1, 2, 3], "every leg must run in order")
        self.assertEqual([s[1] for s in seen], ["walk", "city", "walk"])
        end = self.link.sent[-1]
        self.assertLess(distance(end[0], end[1], 55.75114, 37.60000), 5.0)

    async def test_pause_holds_a_journey_between_legs(self):
        """Pausing must freeze the trip wherever it is, mid-leg or not."""
        legs = [
            {"points": [[55.75000, 37.60000], [55.75007, 37.60000]], "profile": "walk", "note": "a"},
            {"points": [[55.75007, 37.60000], [55.75107, 37.60000]], "profile": "city", "note": "b"},
        ]
        await self.session.follow_journey(legs, from_current=False)
        await asyncio.sleep(1.2)

        await self.session.pause()
        frozen = len(self.link.sent)
        leg_at_pause = self.session.state()["journey"]["leg"]
        await asyncio.sleep(1.8)
        self.assertEqual(len(self.link.sent), frozen, "a paused journey must not move")
        self.assertEqual(self.session.state()["journey"]["leg"], leg_at_pause,
                         "and must not skip to the next leg either")

        await self.session.resume()
        await asyncio.sleep(1.4)
        self.assertGreater(len(self.link.sent), frozen, "resuming carries on")
        await self.session.stop()

    async def test_plain_route_clears_a_running_journey(self):
        await self.session.follow_journey(
            [{"points": [[55.75, 37.60], [55.76, 37.60]], "profile": "walk", "note": "a"}],
            from_current=False,
        )
        self.assertIsNotNone(self.session.state()["journey"])
        await self.session.follow(points=[(55.75, 37.61), (55.76, 37.61)], from_current=False)
        self.assertIsNone(self.session.state()["journey"])
        await self.session.stop()

    async def test_route_resumes_after_the_device_comes_back(self):
        """A yanked cable should pause the route, not destroy it."""
        from iosloc import session as module

        original_interval = module.RECONNECT_INTERVAL_SECONDS
        module.RECONNECT_INTERVAL_SECONDS = 0.1
        try:
            await self.session.follow(
                points=[(55.7558, 37.6176), (55.8000, 37.6176)],
                profile="highway",
                from_current=False,
            )
            await asyncio.sleep(1.4)
            before = len(self.link.sent)
            self.assertGreater(before, 0, "the route should have started")

            # The device disappears for a couple of ticks, then returns.
            self.link.fail_sends = 2
            await asyncio.sleep(3.0)

            self.assertGreater(len(self.link.sent), before, "the route must carry on")
            self.assertIsNone(self.session.state()["error"], "a recovered drop is not an error")
            self.assertFalse(self.session.state()["reconnecting"])
        finally:
            module.RECONNECT_INTERVAL_SECONDS = original_interval
            await self.session.stop()

    async def test_heartbeat_keeps_an_idle_channel_warm(self):
        """With nothing moving, the link still sees traffic."""
        from iosloc import session as module

        original = module.HEARTBEAT_SECONDS
        module.HEARTBEAT_SECONDS = 0.2
        try:
            await self.session.disconnect(restore=False)
            await self.session.connect()
            self.link = FakeLink.instances[-1]
            cleared_before = self.link.cleared
            await asyncio.sleep(0.9)
            self.assertGreater(self.link.cleared, cleared_before,
                               "an idle link should be poked by the heartbeat")
        finally:
            module.HEARTBEAT_SECONDS = original

    async def test_heartbeat_leaves_an_active_override_alone(self):
        """It must never clear a location the user actually set."""
        from iosloc import session as module

        original = module.HEARTBEAT_SECONDS
        module.HEARTBEAT_SECONDS = 0.2
        try:
            await self.session.teleport(55.75, 37.61, profile="stand")
            cleared_before = self.link.cleared
            await asyncio.sleep(0.9)
            self.assertEqual(self.link.cleared, cleared_before,
                             "the heartbeat must not clear an active override")
            self.assertTrue(self.session.state()["override_active"])
        finally:
            module.HEARTBEAT_SECONDS = original

    async def test_commands_need_a_connection(self):
        await self.session.disconnect(restore=False)
        with self.assertRaises(errors.NotConnectedError):
            await self.session.teleport(55.75, 37.61)
        await self.session.connect()
        self.link = FakeLink.instances[-1]

    async def test_bad_coordinates_rejected_before_any_send(self):
        with self.assertRaises(ValueError):
            await self.session.teleport(1000.0, 37.61)
        self.assertEqual(self.link.sent, [])

    async def test_route_needs_at_least_one_point(self):
        with self.assertRaises(ValueError):
            await self.session.follow(points=[], from_current=False)


class DeadChannelTests(unittest.IsolatedAsyncioTestCase):
    """The DTX channel dies from idleness; the link has to survive that.

    Symptom in the wild: connect, spend a few minutes picking a place on the
    map, then move the device -- and the write fails with "Channel is closed"
    because the phone closed the channel while nothing was being sent.
    """

    def test_transport_failures_are_recognised(self):
        from iosloc.device import _is_dead_channel

        for exc in (
            Exception("Channel is closed"),
            Exception("ConnectionTerminatedError: connection terminated"),
            ConnectionResetError("reset by peer"),
            BrokenPipeError(),
            OSError("transport closed"),
        ):
            with self.subTest(error=exc):
                self.assertTrue(_is_dead_channel(exc))

    def test_real_failures_are_not_mistaken_for_a_dead_channel(self):
        """A rejected request must surface, not trigger an endless reconnect."""
        from iosloc.device import _is_dead_channel

        for exc in (ValueError("latitude 91 is outside [-90, 90]"),
                    KeyError("UniqueChipID"),
                    RuntimeError("developer mode is disabled")):
            with self.subTest(error=exc):
                self.assertFalse(_is_dead_channel(exc))

    async def test_a_dead_channel_is_reopened_and_the_write_retried(self):
        from iosloc.device import DeviceLink

        class Backend:
            def __init__(self, fail_first): self.fail_first = fail_first; self.sent = []
            async def set(self, lat, lon):
                if self.fail_first:
                    self.fail_first = False
                    raise Exception("ConnectionTerminatedError: Channel is closed")
                self.sent.append((lat, lon))
            async def clear(self): pass

        link = DeviceLink()
        link._backend = Backend(fail_first=True)
        reopened = []

        async def fake_reopen():
            reopened.append(True)
            link._backend = Backend(fail_first=False)

        link._reopen = fake_reopen
        await link.set(55.75, 37.62)

        self.assertEqual(len(reopened), 1, "the link should have been rebuilt once")
        self.assertEqual(link._backend.sent, [(55.75, 37.62)], "the write must be retried")

    async def test_a_second_failure_is_reported(self):
        """If it is still broken after reopening, the user hears about it."""
        from iosloc.device import DeviceLink

        class AlwaysDead:
            async def set(self, lat, lon):
                raise Exception("Channel is closed")

        link = DeviceLink()
        link._backend = AlwaysDead()

        async def fake_reopen():
            link._backend = AlwaysDead()

        link._reopen = fake_reopen
        with self.assertRaises(Exception) as caught:
            await link.set(55.75, 37.62)
        self.assertIn("Channel is closed", str(caught.exception))


class AirportTests(unittest.TestCase):
    """The airport database and the journey planner."""

    def test_database_is_bundled_and_sane(self):
        from iosloc.airports import load_airports

        airports = load_airports()
        self.assertGreater(len(airports), 500, "the bundled airport list looks truncated")
        for airport in airports[:50]:
            self.assertEqual(len(airport.iata), 3)
            self.assertTrue(-90 <= airport.lat <= 90)
            self.assertTrue(-180 <= airport.lon <= 180)

    def test_search_by_code_city_and_accentless_name(self):
        from iosloc.airports import find_airports

        self.assertEqual(find_airports("SVO")[0].iata, "SVO")
        self.assertTrue(any(a.iata == "DXB" for a in find_airports("dubai")))
        # "istanbul" must match "İstanbul": folding has to strip the accent.
        self.assertTrue(any(a.cc == "TR" for a in find_airports("istanbul")))

    def test_an_exact_code_wins_outright(self):
        """Typing IST must mean Istanbul, not every airport in Afghanistan."""
        from iosloc.airports import find_airports

        results = find_airports("IST")
        self.assertEqual([a.iata for a in results], ["IST"])

    def test_short_queries_do_not_match_country_names(self):
        """"ist" appears inside Afghanistan, Pakistan and Uzbekistan."""
        from iosloc.airports import find_airports

        for airport in find_airports("dme"):
            self.assertEqual(airport.iata, "DME")

    def test_city_search_still_finds_every_airport(self):
        from iosloc.airports import find_airports

        moscow = {a.iata for a in find_airports("moscow")}
        self.assertIn("SVO", moscow)
        self.assertIn("DME", moscow)

    def test_nearest_airport_and_exclusion(self):
        from iosloc.airports import nearest_airport

        moscow = nearest_airport(55.75, 37.62)
        self.assertEqual(moscow.cc, "RU")
        other = nearest_airport(55.75, 37.62, exclude=(moscow.iata,))
        self.assertNotEqual(other.iata, moscow.iata)

    def test_short_trip_stays_on_the_ground(self):
        from iosloc.airports import plan_journey

        legs = plan_journey((55.75, 37.62), (55.43, 37.55))
        self.assertEqual(len(legs), 1)
        self.assertNotEqual(legs[0].profile, "plane")

    def test_long_trip_becomes_drive_fly_drive(self):
        from iosloc.airports import plan_journey

        legs = plan_journey((55.70, 37.55), (25.2048, 55.2708))
        profiles = [leg.profile for leg in legs]
        self.assertIn("plane", profiles)
        self.assertEqual(profiles.count("plane"), 1, "exactly one flight leg")
        flight = next(leg for leg in legs if leg.profile == "plane")
        self.assertGreater(flight.length, 1_000_000)
        # The flight must be the bulk of the trip, not a detour.
        self.assertGreater(flight.length, sum(l.length for l in legs if l is not flight))

    def test_legs_join_end_to_end(self):
        """Each leg has to start where the previous one stopped."""
        from iosloc.airports import plan_journey
        from iosloc.geo import distance

        legs = plan_journey((48.8566, 2.3522), (35.6762, 139.6503))  # Paris -> Tokyo
        for before, after in zip(legs, legs[1:]):
            gap = distance(*before.points[-1], *after.points[0])
            self.assertLess(gap, 1.0, "legs must be continuous")

    def test_multi_stop_chains_flights_between_airports(self):
        from iosloc.airports import plan_multi_stop

        legs = plan_multi_stop(["SVO", "DXB", "NRT"])
        self.assertEqual([leg.profile for leg in legs], ["plane", "plane"])
        self.assertIn("SVO", legs[0].note)
        self.assertIn("NRT", legs[1].note)

    def test_multi_stop_adds_ground_legs_at_the_ends(self):
        from iosloc.airports import plan_multi_stop

        legs = plan_multi_stop(["SVO", "DXB"], start=(55.70, 37.55), finish=(25.07, 55.14))
        self.assertNotEqual(legs[0].profile, "plane", "it starts on the ground")
        self.assertNotEqual(legs[-1].profile, "plane", "and ends on the ground")
        self.assertTrue(any(leg.profile == "plane" for leg in legs))

    def test_a_distant_start_flies_in_rather_than_driving(self):
        """Starting in Vladivostok must not produce a 6000 km drive to Moscow."""
        from iosloc.airports import plan_multi_stop

        legs = plan_multi_stop(["SVO", "DXB"], start=(43.1155, 131.8855))
        ground = [leg for leg in legs if leg.profile != "plane"]
        for leg in ground:
            self.assertLess(leg.length, 300_000, f"{leg.note} is too far to drive")
        self.assertGreaterEqual(len([l for l in legs if l.profile == "plane"]), 2)

    def test_neighbouring_airports_are_driven_not_flown(self):
        from iosloc.airports import plan_multi_stop

        # Both Moscow airports: flying between them would be ridiculous.
        legs = plan_multi_stop(["SVO", "DME"])
        self.assertEqual(legs[0].profile, "highway")

    def test_unknown_code_is_refused(self):
        from iosloc.airports import plan_multi_stop

        with self.assertRaises(ValueError):
            plan_multi_stop(["SVO", "ZZZZ"])

    def test_every_bundled_preset_can_be_planned(self):
        """A preset that cannot be planned is a button that fails when pressed."""
        from iosloc.airports import load_presets, plan_multi_stop

        presets = load_presets()
        self.assertGreater(len(presets), 5)
        for preset in presets:
            with self.subTest(preset=preset["id"]):
                legs = plan_multi_stop(preset["stops"])
                self.assertTrue(legs)
                self.assertIn("ru", preset["name"])
                self.assertIn("en", preset["name"])

    def test_force_flight_overrides_the_distance_rule(self):
        from iosloc.airports import plan_journey

        near = ((55.75, 37.62), (55.43, 37.55))
        self.assertNotIn("plane", [l.profile for l in plan_journey(*near)])
        forced = plan_journey(*near, force_flight=True)
        self.assertTrue(any(l.profile == "plane" for l in forced) or len(forced) == 1)


class ServerShapeTests(unittest.TestCase):
    """The API surface the UI depends on must exist and stay loopback-only."""

    def test_expected_routes_are_registered(self):
        from iosloc.server import app

        paths = {route.path for route in app.routes}
        for path in (
            "/api/state", "/api/profiles", "/api/devices", "/api/connect",
            "/api/disconnect", "/api/teleport", "/api/route", "/api/steer",
            "/api/profile", "/api/speed", "/api/pause", "/api/resume",
            "/api/stop", "/api/search", "/api/snap", "/api/minimize",
            "/api/reveal-devmode", "/api/log", "/",
        ):
            self.assertIn(path, paths, f"missing route {path}")

    def test_static_assets_are_present(self):
        from iosloc.server import STATIC_DIR

        for name in (
            "index.html", "app.css", "app.js",
            # Both map engines ship: raster works everywhere, vector needs WebGL.
            "manifest.webmanifest",
            "vendor/leaflet.js", "vendor/leaflet.css",
            "vendor/maplibre-gl.js", "vendor/maplibre-gl.css",
            "map-styles/liberty.json", "map-styles/positron.json", "map-styles/dark.json",
            "brand/ios-loc.ico", "brand/icon-1024.png", "brand/icon-128.png",
        ):
            self.assertTrue((STATIC_DIR / name).is_file(), f"missing {name}")

    def test_every_vector_style_in_the_ui_is_shipped(self):
        """Each vector choice in the settings panel needs its style file."""
        import re

        from iosloc.server import STATIC_DIR

        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        offered = set(re.findall(r'id="set-mapstyle".*?</div>', html, re.S)[0:1] and
                      re.findall(r'data-value="(\w+)"',
                                 re.findall(r'id="set-mapstyle".*?</div>', html, re.S)[0]))
        self.assertIn("osm", offered, "the always-works raster map must be offered")
        for style in re.findall(r'style: "/static/(map-styles/[\w.-]+)"', app_js):
            self.assertTrue((STATIC_DIR / style).is_file(), f"missing {style}")

    def test_windows_icon_carries_every_size(self):
        """The .ico must hold each size Explorer and the taskbar ask for."""
        from PIL import Image

        from iosloc.server import STATIC_DIR

        with Image.open(STATIC_DIR / "brand" / "ios-loc.ico") as icon:
            sizes = {size[0] for size in icon.info.get("sizes", set())}
        for expected in (16, 32, 48, 256):
            self.assertIn(expected, sizes, f"icon is missing the {expected}px variant")

    def test_serve_defaults_to_loopback(self):
        """Without --lan the panel must stay unreachable from the network."""
        import inspect

        from iosloc.server import serve

        source = inspect.getsource(serve)
        self.assertIn('host = "127.0.0.1"', source)
        # The only place that widens the binding is the LAN branch, and it must
        # mint a token in the same breath.
        lan_branch = source.split("if lan:", 1)[1]
        self.assertIn('host = "0.0.0.0"', lan_branch)
        self.assertIn("generate_token()", lan_branch)


class VerifyStateTests(unittest.TestCase):
    """The panel's proof that fixes are reaching the device."""

    def test_age_is_none_before_anything_is_sent(self):
        session = LocationSession()
        state = session.state()
        self.assertIsNone(state["last_fix_age"])
        self.assertEqual(state["fixes_sent"], 0)

    def test_age_tracks_the_last_accepted_fix(self):
        import time

        session = LocationSession()
        # A fix that was accepted a minute ago must read as a minute old: a
        # stalled stream keeps its counter, so only the age gives it away.
        session._last_sent_at = time.monotonic() - 60.0
        state = session.state()
        self.assertIsNotNone(state["last_fix_age"])
        self.assertGreaterEqual(state["last_fix_age"], 59.0)


class ConsoleTests(unittest.TestCase):
    """The window has to say that it is running, whatever the terminal is."""

    def test_banner_names_the_panel_and_the_log(self):
        import io
        import contextlib
        from iosloc.console import print_banner

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            print_banner("http://127.0.0.1:8723/", 8723, "C:/x/ios-loc.log", "9.9.9")
        text = buffer.getvalue()

        self.assertIn("RUNNING", text)
        self.assertIn("http://127.0.0.1:8723/", text)
        self.assertIn("ios-loc.log", text)
        self.assertIn("9.9.9", text)
        # The reason the banner exists at all.
        self.assertIn("Keep this window open", text)

    def test_banner_survives_a_missing_log_path(self):
        import io
        import contextlib
        from iosloc.console import print_banner

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            print_banner("http://127.0.0.1:8723/", 8723, None, "1.0")
        self.assertIn("unavailable", buffer.getvalue())

    def test_console_handler_only_echoes_our_own_records(self):
        import logging
        from iosloc.console import attach_console_log

        root = logging.getLogger()
        before = list(root.handlers)
        try:
            attach_console_log()
            handler = root.handlers[-1]
            ours = logging.LogRecord("iosloc.session", logging.INFO, "f", 1, "hi", None, None)
            theirs = logging.LogRecord("urllib3", logging.INFO, "f", 1, "noise", None, None)
            self.assertTrue(handler.filter(ours))
            self.assertFalse(handler.filter(theirs))
        finally:
            root.handlers[:] = before

    def test_setting_the_title_never_raises(self):
        from iosloc.console import set_console_title

        set_console_title("ios-loc test")


class AccessTests(unittest.TestCase):
    """LAN mode exposes device control to the network, so the gate must hold."""

    def test_tokens_are_random_and_long_enough(self):
        from iosloc.access import TOKEN_ALPHABET, TOKEN_LENGTH, generate_token

        tokens = {generate_token() for _ in range(200)}
        self.assertEqual(len(tokens), 200, "tokens must not repeat")
        for token in list(tokens)[:20]:
            self.assertEqual(len(token), TOKEN_LENGTH)
            self.assertTrue(set(token) <= set(TOKEN_ALPHABET))

    def test_local_addresses_exclude_loopback(self):
        import ipaddress

        from iosloc.access import local_addresses

        for address in local_addresses():
            parsed = ipaddress.IPv4Address(address)
            self.assertFalse(parsed.is_loopback, f"{address} is loopback")
            self.assertFalse(parsed.is_link_local, f"{address} is link-local")

    def test_home_wifi_addresses_are_offered_first(self):
        """A VPN or Hyper-V address must not outrank the real Wi-Fi one."""
        from iosloc.access import _address_rank

        ordered = sorted(["10.0.0.1", "172.20.0.1", "192.168.1.50"], key=_address_rank)
        self.assertEqual(ordered[0], "192.168.1.50")

    def test_token_is_required_once_lan_mode_is_on(self):
        from iosloc import server

        self.assertIsNone(server.access_token, "loopback mode must need no token")
        source = __import__("inspect").getsource(server.require_token)
        self.assertIn("compare_digest", source, "the token must be compared in constant time")
        self.assertIn("401", source)

    def test_qr_encodes_the_url(self):
        from iosloc.access import qr_png

        png = qr_png("http://192.168.1.50:8723/?k=test")
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(bytes([0x89]) + b"PNG"), "should be a PNG")


class TrayTests(unittest.TestCase):
    """The tray is a convenience; it must never be able to break the panel."""

    def test_console_helpers_never_raise(self):
        from iosloc import tray

        # On a machine with no console these return False rather than throwing.
        self.assertIsInstance(tray.console_window_available(), bool)
        self.assertIsInstance(tray.hide_console(), bool)
        self.assertIsInstance(tray.show_console(), bool)

    def test_tray_icon_image_is_shipped(self):
        from iosloc import tray

        self.assertTrue(tray.ICON_PATH.is_file(), "the tray icon image is missing")

    def test_start_tray_survives_a_missing_backend(self):
        """With pystray unimportable, start_tray returns None instead of raising."""
        import builtins

        from iosloc import tray

        real_import = builtins.__import__

        def refuse_pystray(name, *args, **kwargs):
            if name == "pystray":
                raise ImportError("simulated: no tray backend")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = refuse_pystray
        try:
            result = tray.start_tray("http://127.0.0.1:8723/", lambda: None, lambda: None)
        finally:
            builtins.__import__ = real_import
        self.assertIsNone(result)


class DoctorTests(unittest.TestCase):
    def test_every_required_module_imports(self):
        """The same checklist the frozen build is verified against."""
        import importlib

        from iosloc.cli import REQUIRED_MODULES

        self.assertGreaterEqual(len(REQUIRED_MODULES), 5)
        for name, purpose in REQUIRED_MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)
                self.assertTrue(purpose, "each module needs a human-readable purpose")


class PortHandlingTests(unittest.TestCase):
    """A busy port must never end in a window that closes without a word."""

    def _busy_port(self):
        """Bind a port and keep it, like an unrelated program would."""
        import socket

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        return listener.getsockname()[1]

    def test_busy_port_is_detected(self):
        from iosloc.server import _port_is_free

        self.assertFalse(_port_is_free(self._busy_port()))

    def test_free_port_is_detected(self):
        import socket

        from iosloc.server import _port_is_free

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertTrue(_port_is_free(port))

    def test_foreign_listener_is_not_mistaken_for_a_panel(self):
        from iosloc.server import _panel_already_running

        self.assertFalse(_panel_already_running(self._busy_port()))

    def test_explicit_busy_port_fails_loudly_instead_of_serving(self):
        from iosloc import server

        port = self._busy_port()
        called = []
        original = server.webbrowser.open
        server.webbrowser.open = lambda url: called.append(url)
        self.addCleanup(lambda: setattr(server.webbrowser, "open", original))

        self.assertEqual(server.serve(port=port, open_browser=False), 1)
        self.assertEqual(called, [], "nothing should have been opened")


if __name__ == "__main__":
    unittest.main(verbosity=2)
