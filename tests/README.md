# CARS Tests

## Live durability harness

This is an integration harness connected to ROS 2/Gazebo, with controlled fault
injection for selected conditions. Results depend on spawn pose, sensor freshness,
and the active world's obstacles. It is not an isolated unit-test suite.

| Scenario | Method | Environment dependence |
| --- | --- | --- |
| Sensor/ROS loss | Temporarily replace connection, scan, or odometry timestamp | Some cases require a working ROS baseline |
| Turn/move timeout | Temporarily reduce timeouts to 0.05 seconds | Requires fresh sensors and unblocked motion |
| Forward obstacle | Request a longer forward move and inspect the safety result | Depends on obstacle placement |
| Rear obstacle | Request a short reverse move | Depends on rear obstacles |
| Successful motion | Small turn and forward move | Requires a clear route and fresh sensors |

The harness imports Autopilot directly and does not use MCP transport. Keep
these constants at module scope so tests can temporarily override them:

```python
MAX_TURN_DEGREES = 60.0
MAX_DISTANCE_METERS = 3.5
FRONT_STOP_THRESHOLD_METERS = 0.25
SAFETY_RADIUS_METERS = 0.20
MAX_TURN_DURATION_SECONDS = 10.0
MAX_MOVE_DURATION_SECONDS = 25.0
```

Production values are restored after the timeout cases. A sensor or obstacle
stop before the timeout does not verify the expected timeout behavior.

Launch the simulation and tests from the repository root:

```bash
./tests/run_tests.sh
```

If Gazebo and rosbridge are already running:

```bash
PYTHONPATH="$PWD/modules" python3 tests/test_durability.py
```

The harness also adds `modules/` to its import path. Forward/reverse test distances
can be configured with English environment variable names:

```bash
CARS_TEST_FORWARD_DISTANCE=3.5 CARS_TEST_REVERSE_DISTANCE=-1.0 \
PYTHONPATH="$PWD/modules" python3 tests/test_durability.py
```

An open forward route or obstacle-free reverse move skips obstacle verification;
it does not pass it. A safety stop in the normal-motion case likewise does not
prove successful unobstructed motion. Use controlled, repeatable spawn positions
for reliable integration evidence. Turn and translation success are classified
from their separate result lines.

Every run creates a fresh experiment ID. Tools share the active run; the JSONL
contains tool start/end events, individual `test_result` records, and a counted
`test_summary`. Failures take precedence over skips, and the shell launcher
returns the harness status unchanged:

| Exit code | Meaning |
| --- | --- |
| 0 | All cases passed; none skipped |
| 1 | At least one failure |
| 2 | Setup/import error |
| 3 | No failures, but skipped cases or no results; incomplete verification |

## Offline regression suite

```bash
python3 -m unittest discover -s tests -p test_experiment_log.py -v
```

The logging suite uses disposable log/memory directories and fake ROS while
exercising real FastMCP registration and transport. It verifies all 20 tools,
the camera-to-memory-to-map workflow, process-safe sequence numbers, run rotation,
exception recording, image summaries, and result classification. It also
verifies detailed-camera JPEG delivery over MCP, resizing, malformed-frame
errors, and exclusion of image pixels from logs.

These checks do not replace the live Gazebo motion tests. Do not use unrestricted
`unittest discover` with the live harness pattern: importing `test_durability.py`
starts a live run. Select the offline filenames explicitly as above.


## Model telemetry regressions

Run `python3 -m unittest discover -s tests -p test_model_metrics.py -v` without
ROS or API credentials. These tests cover timing, usage validation, missing
values, retries/errors, run rotation, stage markers, byte/character accounting,
and the importer/report CLI. See `METRICS.md` for measurement boundaries.
