#!/usr/bin/env python3
# ==========================================================================
# CARS - Durability Test Harness
#
# Import autopilot_mcp.py directly and invoke its tools.
# FastMCP usually preserves the decorated function; _invoke also supports
# the .fn wrapper used by versions with a different decoration behavior.
# This harness does not exercise the MCP transport.
#
# PREREQUISITE: Motion limits and timeout constants in autopilot_mcp.py
# must remain at module scope so tests can temporarily override them.
# Otherwise Test 2 cannot exercise timeouts.
#
# Tests 3 and 4 depend on obstacles in the live simulation.
# The obstacle arrangement at the spawn pose is not known in advance.
# Report unverified cases as skipped rather than assuming success.
# ==========================================================================

import sys
import os
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES_DIR = os.path.join(PROJECT_ROOT, "modules")
sys.path.insert(0, MODULES_DIR)

from experiment_log import start_run, write_event
from test_summary import summarize, motion_completed

RUN_ID = start_run(metadata={"kind": "durability_tests"})
print(f"Experiment run-id: {RUN_ID}")
RESULTS = []
PASSED = []
FAILED = []
SKIPPED = []


def print_heading(name):
    print(f"\n{'='*60}\n> {name}\n{'='*60}")


def record_result(name, passed, detail=""):
    record = {"name": name, "status": "passed" if passed is True else "failed" if passed is False else "skipped", "detail": detail}
    RESULTS.append(record)
    write_event(dict(record, event="test_result"), RUN_ID)
    if passed is True:
        print(f"  PASSED - {detail}")
        PASSED.append(name)
    elif passed is False:
        print(f"  FAILED - {detail}")
        FAILED.append(name)
    else:  # None means the case was skipped.
        print(f"  SKIPPED - {detail}")
        SKIPPED.append(name)


def _invoke(tool_obj, *args, **kwargs):
    """Invoke the function directly or through .fn for FastMCP version compatibility."""
    return getattr(tool_obj, "fn", tool_obj)(*args, **kwargs)


# --------------------------------------------------------------------------
# Importing the module also attempts a real ROS connection.
# --------------------------------------------------------------------------
print("Importing autopilot_mcp (a real ROS connection will be attempted)...")
try:
    import autopilot_mcp as ap
except Exception as e:
    print(f"CRITICAL: Could not import autopilot_mcp.py: {e}")
    write_event({"event": "test_summary", "status": "error", "exit_code": 2, "detail": str(e)}, RUN_ID)
    sys.exit(2)

print("Waiting 3 seconds for the ROS connection and initial sensor data...")
time.sleep(3.0)


# ==========================================================================
# TEST 1 - SENSOR / ROS CONNECTION LOSS
# ==========================================================================
print_heading("TEST 1: Block motion on sensor or ROS connection loss")


class _DisconnectedRos:
    is_connected = False


try:
    # 1a: Simulate a missing ROS connection.
    original_ros_client = ap.ros_client
    ap.ros_client = _DisconnectedRos()
    result = _invoke(ap.move_robot, 0.0, 0.3)
    ap.ros_client = original_ros_client  # Restore the original connection.

    passed = "MOTION BLOCKED" in result and "No ROS connection" in result
    record_result("1a-no_ros_connection", passed, result.splitlines()[0])
except Exception as e:
    ap.ros_client = original_ros_client
    record_result("1a-no_ros_connection", False, f"Unexpected error: {e}")

try:
    # 1b: Simulate missing Lidar data.
    original_scan = ap.latest_scan_ranges
    ap.latest_scan_ranges = None
    result = _invoke(ap.move_robot, 0.0, 0.3)
    ap.latest_scan_ranges = original_scan

    passed = "MOTION BLOCKED" in result and "No Lidar data has been received" in result
    record_result("1b-no_lidar_data", passed, result.splitlines()[0])
except Exception as e:
    ap.latest_scan_ranges = original_scan
    record_result("1b-no_lidar_data", False, f"Unexpected error: {e}")

try:
    # 1c: Make odometry 2 seconds old, exceeding MAX_SENSOR_AGE_SECONDS=1.0.
    original_odom_timestamp = ap.last_odom_update
    ap.last_odom_update = time.time() - 2.0
    result = _invoke(ap.move_robot, 0.0, 0.3)
    ap.last_odom_update = original_odom_timestamp

    passed = "MOTION BLOCKED" in result and "Odometry data" in result and "stale" in result
    record_result("1c-stale_odometry", passed, result.splitlines()[0])
except Exception as e:
    ap.last_odom_update = original_odom_timestamp
    record_result("1c-stale_odometry", False, f"Unexpected error: {e}")


# ==========================================================================
# TEST 2 - TURN AND TRANSLATION TIMEOUTS
# ==========================================================================
print_heading("TEST 2: Turn and translation timeouts")

if not hasattr(ap, "MAX_TURN_DURATION_SECONDS") or not hasattr(ap, "MAX_MOVE_DURATION_SECONDS"):
    record_result(
        "2-Timeout",
        None,
        "MAX_TURN_DURATION_SECONDS / MAX_MOVE_DURATION_SECONDS are not at module scope. "
        "In autopilot_mcp.py, move these constants out of the function "
        "and into module scope to run this test."
    )
else:
    try:
        original_turn_timeout = ap.MAX_TURN_DURATION_SECONDS
        original_move_timeout = ap.MAX_MOVE_DURATION_SECONDS

        # Set very short limits so the operation times out before motion can finish.
        ap.MAX_TURN_DURATION_SECONDS = 0.05
        ap.MAX_MOVE_DURATION_SECONDS = 0.05

        turn_result = _invoke(ap.move_robot, 45.0, 0.0)
        move_result = _invoke(ap.move_robot, 0.0, 1.0)

        ap.MAX_TURN_DURATION_SECONDS = original_turn_timeout
        ap.MAX_MOVE_DURATION_SECONDS = original_move_timeout

        turn_passed = "TIMEOUT" in turn_result and "TURN TIMEOUT" in turn_result
        move_passed = "TIMEOUT" in move_result and "DISTANCE TIMEOUT" in move_result

        record_result("2a-turn_timeout", turn_passed, turn_result.splitlines()[-2] if turn_passed else turn_result[:120])
        record_result("2b-distance_timeout", move_passed, move_result.splitlines()[-2] if move_passed else move_result[:120])
    except Exception as e:
        ap.MAX_TURN_DURATION_SECONDS = original_turn_timeout
        ap.MAX_MOVE_DURATION_SECONDS = original_move_timeout
        record_result("2-Timeout", False, f"Unexpected error: {e}")


# ==========================================================================
# TEST 3 - FORWARD OBSTACLE (ENVIRONMENT DEPENDENT)
# ==========================================================================
print_heading("TEST 3: Forward obstacle detection (environment dependent, best effort)")
print("  This test depends on whether there is an obstacle ahead of the robot.")
print("  To create a reproducible scenario, spawn the robot close to a wall")
print("  in Gazebo or increase FORWARD_DISTANCE.")

FORWARD_DISTANCE = float(os.environ.get("CARS_TEST_FORWARD_DISTANCE", "3.0"))

try:
    result = _invoke(ap.move_robot, 0.0, FORWARD_DISTANCE)
    if "LIVE OBSTACLE DETECTED" in result:
        record_result("3-forward_obstacle", True, "An obstacle was detected during motion and the robot stopped.")
    elif "LIMIT" in result:
        record_result("3-forward_obstacle", None, "The command was limited; obstacle detection was not verified.")
    elif motion_completed(result, "Translation"):
        record_result("3-forward_obstacle", None, f"No obstacle within {FORWARD_DISTANCE}m. This direction is clear; detection was not tested.")
    else:
        record_result("3-forward_obstacle", False, f"Unexpected report format: {result[:150]}")
except Exception as e:
    record_result("3-forward_obstacle", False, f"Unexpected error: {e}")


# ==========================================================================
# TEST 4 - REVERSE MOTION (180-DEGREE SAFETY CHECK)
# ==========================================================================
print_heading("TEST 4: Reverse motion (180-degree safety check, best effort)")

REVERSE_DISTANCE = float(os.environ.get("CARS_TEST_REVERSE_DISTANCE", "-0.5"))

try:
    result = _invoke(ap.move_robot, 0.0, REVERSE_DISTANCE)
    if "LIVE OBSTACLE DETECTED" in result:
        record_result("4-rear_obstacle", True, "A rear obstacle was detected correctly and motion stopped.")
    elif motion_completed(result, "Translation"):
        record_result("4-rear_obstacle", None, "Reverse motion completed; no rear obstacle was present, so detection was not verified.")
    else:
        record_result("4-rear_obstacle", False, f"Unexpected report format: {result[:150]}")
except Exception as e:
    record_result("4-rear_obstacle", False, f"Unexpected error: {e}")


# ==========================================================================
# TEST 5 - SUCCESSFUL MOTION (HEALTH CHECK)
# ==========================================================================
print_heading("TEST 5: Successful motion and measured distance under normal conditions")

try:
    # Start with a small turn below the limit.
    turn_result = _invoke(ap.move_robot, 10.0, 0.0)
    turn_ok = motion_completed(turn_result, "Turn")

    # Request a short forward move. Stopping for an obstacle is safe,
    # but does not prove that normal motion completed successfully.
    move_result = _invoke(ap.move_robot, 0.0, 0.2)
    move_ok = motion_completed(move_result, "Translation")

    record_result("5a-successful_turn", turn_ok, turn_result.splitlines()[1] if turn_ok else turn_result[:150])
    if "LIVE OBSTACLE DETECTED" in move_result:
        record_result(
            "5b-successful_motion",
            None,
            "An obstacle was present ahead; the safety layer stopped correctly, but normal motion was not verified."
        )
    else:
        record_result("5b-successful_motion", move_ok, move_result.splitlines()[1] if move_ok else move_result[:150])
except Exception as e:
    record_result("5-successful_motion", False, f"Unexpected error: {e}")


# ==========================================================================
# SUMMARY
# ==========================================================================
print_heading("SUMMARY")
print(f"Passed : {len(PASSED)}  {PASSED}")
print(f"Skipped: {len(SKIPPED)}  {SKIPPED}")
print(f"Failed : {len(FAILED)}  {FAILED}")

summary = summarize(RESULTS)
write_event(dict(summary, event="test_summary"), RUN_ID)
print(f"Result: {summary['status']} | run-id: {RUN_ID}")
sys.exit(summary['exit_code'])
