#!/bin/bash
# CARS - Durability Test Suite
# Launch Gazebo and rosbridge, run the live harness, and clean up owned processes.

# Do not use set -e: summarize the harness exit status even when tests fail.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
GAZEBO_PID=""
ROSBRIDGE_PID=""

# Stop only processes started by this script, on normal exit or interruption.
cleanup() {
    echo -e "\nStopping the test suite and its background processes..."
    for pid in "$ROSBRIDGE_PID" "$GAZEBO_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            pkill -TERM -P "$pid" 2>/dev/null || true
            kill -TERM "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT INT TERM

echo "=========================================================="
echo "CARS Durability Tests - Preparing the environment"
echo "=========================================================="

source /opt/ros/humble/setup.bash
export TURTLEBOT3_MODEL=waffle

echo "Starting the TurtleBot3 House simulation..."
ros2 launch turtlebot3_gazebo turtlebot3_house.launch.py &
GAZEBO_PID=$!
sleep 8

echo "Starting the rosbridge WebSocket server..."
ros2 launch rosbridge_server rosbridge_websocket_launch.xml &
ROSBRIDGE_PID=$!
sleep 4

# Check whether rosbridge is listening.
echo "Checking rosbridge availability on port 9090..."
if command -v nc >/dev/null 2>&1; then
    if nc -z -w 3 127.0.0.1 9090; then
        echo "Rosbridge port is open."
    else
        echo "Rosbridge was unreachable within 3 seconds. Tests will run but may fail."
    fi
else
    echo "Command 'nc' not found; skipping the port check. Install netcat to enable it."
fi

echo ""
echo "=========================================================="
echo "Running tests"
echo "=========================================================="

cd "$PROJECT_ROOT" || exit 1
PYTHONPATH="$PROJECT_ROOT/modules${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "$SCRIPT_DIR/test_durability.py"
TEST_EXIT_CODE=$?

echo ""
echo "=========================================================="
case "$TEST_EXIT_CODE" in
    0) echo "ALL TESTS PASSED (none skipped)" ;;
    3) echo "INCOMPLETE: Some scenarios were skipped; verification is incomplete." ;;
    1) echo "SOME TESTS FAILED (see details above)" ;;
    *) echo "TEST SUITE EXECUTION ERROR (code: $TEST_EXIT_CODE)" ;;
esac
echo "=========================================================="
exit "$TEST_EXIT_CODE"
