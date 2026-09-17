# CARS - Cognitive Autonomous Robot System

CARS is an experimental semantic navigation architecture for a mobile robot operating without a persistent dense metric map. It combines online RGB camera observations, 2D LiDAR, odometry, a multimodal language-model agent, and sparse topological memory.

The current prototype runs on ROS 2 Humble with a TurtleBot3 Waffle in Gazebo. Robot capabilities are exposed to the agent through five Model Context Protocol (MCP) servers. The language model performs task-level semantic reasoning; motion execution and immediate collision checks remain in deterministic ROS-facing tools.

> **Project status:** `v0.1` is a simulation-only experimental prototype. It has not been validated on a physical robot and should not be treated as safety-certified navigation software.

## Research question

Can a protocol-grounded vision-language agent complete semantically meaningful navigation tasks using online camera, LiDAR, and odometry observations, while maintaining only sparse topological-semantic memory instead of a persistent dense occupancy map?

CARS does not claim that geometric localization or SLAM is generally unnecessary. It investigates navigation under mapless, mapping-degraded, or task-constrained conditions where a persistent dense metric map is unavailable or undesirable. Comparison with classical and hybrid SLAM/Nav2 baselines is planned future work.

## Architecture

| Component          | Path                               | Responsibility                                                                                      |
| ------------------ | ---------------------------------- | --------------------------------------------------------------------------------------------------- |
| Autopilot          | `modules/autopilot_mcp.py`         | Motion execution, odometry-based feedback, safety checks, target and topological route calculations |
| Camera             | `modules/camera_mcp.py`            | Fast and detailed RGB observations                                                                  |
| LiDAR/Radar        | `modules/lidar_mcp.py`             | 360-degree LiDAR visualization and passage-width analysis                                           |
| Cartographer       | `modules/kartograf_mcp.py`         | Mermaid topological-map output                                                                      |
| Cognitive memory   | `modules/custom_memory_mcp.py`     | Short-term and long-term memory (STM/LTM)                                                           |
| Master protocol    | `rules/CARS_MASTER_PROTOCOL.md`    | Detailed agent behavior and decision rules                                                          |
| System instruction | `rules/CARS_SYSTEM_INSTRUCTION.md` | Operational prompt used by the agent                                                                |

The agent follows a hierarchical loop:

1. Interpret the task and select a high-level route strategy.
2. Read short-term and long-term memory.
3. Combine camera semantics with current LiDAR geometry.
4. Propose a bounded motion or recovery action.
5. Execute the action through the deterministic autopilot.
6. Verify the result and update memory and the topological map.

The current protocol includes explicit procedures for occlusion checks, obstacle bypass, oscillation detection, passage verification, cross-room observations, and sensor-grounded task completion.

## Safety mechanisms in v0.1

The autopilot currently includes:

- fail-closed motion checks when ROS, LiDAR, or odometry is unavailable;
- rejection of stale LiDAR and odometry data;
- odometry-feedback control for rotation and translation;
- bounded rotation and translation commands;
- motion timeouts;
- live frontal-distance monitoring;
- direction-aware near-field LiDAR checks;
- motor stop on sensor or connection loss;
- reporting of measured rotation and travelled distance.

These mechanisms are research safeguards, not formal or certified safety guarantees.

## Requirements

- Ubuntu 22.04
- ROS 2 Humble
- Gazebo with `turtlebot3_gazebo` and `turtlebot3_description`
- `rosbridge_server`
- Python 3.10 or later
- An MCP-compatible multimodal language-model client

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

The v0.1 environment has been verified with `fastmcp` 2.14.2, `mcp` 1.25.0, `roslibpy` 2.0.0, `numpy` 1.26.4, Pillow 10.1.0, and OpenCV (`cv2`) 4.5.4. In the verified Ubuntu/ROS environment, OpenCV is supplied by the Ubuntu `python3-opencv` package. `requirements.txt` pins the Python packages used by the prototype and permits an OpenCV 4.x pip installation for environments that do not provide `cv2` as a system package.

## Tested language models

CARS has been exercised through MCP-compatible clients with the following models:

| Model                       | Current evidence                                                       |
| --------------------------- | ---------------------------------------------------------------------- |
| GPT-6 Astra (`gpt-6-astra`) | Primary model selected for comprehensive use and continued development |
| Gemini 3.8 Flash            | Tested with the CARS MCP tool workflow as an alternative model         |
| Opus 5                      | Tested with the CARS MCP tool workflow as an alternative model         |

Gemini 3.8 Flash and Opus 5 were tested with the MCP workflow; however, GPT-6 Astra was selected for comprehensive use. Future CARS development and experiments will therefore use GPT-6 Astra as the primary model unless a comparison experiment explicitly requires another model.

In this repository, **MCP-compatible** means that the selected client can expose the CARS tools to the model and the model can follow the project protocol, interpret the supplied observations, and issue tool calls. It does not mean that the three models have equivalent navigation accuracy, latency, cost, or safety behavior. A controlled cross-model benchmark has not yet been completed.

For reproducible experiments, record the exact model identifier or snapshot, client/host application, run date, protocol revision, and any exposed reasoning or sampling settings.

## Running the simulation

From the repository root, start Gazebo and rosbridge:

```bash
./start_robot.sh
```

Start each MCP server in a separate terminal, also from the repository root:

```bash
python3 modules/autopilot_mcp.py
python3 modules/camera_mcp.py
python3 modules/lidar_mcp.py
python3 modules/kartograf_mcp.py
python3 modules/custom_memory_mcp.py
```

Configure the MCP-compatible client to use these five servers. Supply the following files as the agent's protocol context:

```text
rules/CARS_MASTER_PROTOCOL.md
rules/CARS_SYSTEM_INSTRUCTION.md
```

The current system instruction defines `wake up` as the diagnostic-start command.

## Tests

The test harness in `tests/` is intended to cover missing or stale sensor data, motion timeouts, forward and rear obstacle responses, and normal motion with measured-result reporting.

Run the Python harness from the repository root with the module directory on `PYTHONPATH`:

```bash
PYTHONPATH="$PWD/modules" python3 tests/test_dayaniklilik.py
```

The integration launcher is:

```bash
./tests/run_tests.sh
```

Some integration cases depend on the robot's spawn pose and active Gazebo world. A skipped environment-dependent test is not evidence that the associated behavior is correct. Before publishing results, use controlled spawn poses and retain the resulting logs. See `tests/README.md` for the current design and known prerequisites.

## Logs and runtime state

Runtime state is intentionally excluded from version control:

- `logs/` contains the shared JSONL records produced by instrumented MCP tools.
- `logs/current_run.txt` identifies which `run_<id>.jsonl` file the MCP server processes share.
- `memory/long_term_memory.json` stores topological-semantic long-term memory.
- `memory/short_term_memory.json` stores the four-entry STM window.
- `memory/kognitif_harita.md` is the generated Mermaid representation.

All five MCP servers use the same active JSONL file. The log and memory paths are resolved from each module's physical file location, so they do not depend on the shell's current working directory.

### Start a controlled experiment

Before each experiment, from the repository root:

```bash
python3 modules/experiment_log.py --metadata '{"scenario":"direct_target","model":"exact-model-id","target":"chair","initial_pose":{"x":0,"y":0,"yaw":0},"protocol_revision":"git-commit","variant":"full"}'
```

The command prints a fresh run-id and atomically switches `logs/current_run.txt`.
Use `--run-id video_01` for a named run; an existing ID is rejected to prevent
accidental experiment mixing. This starts recording context; it does not launch
Gazebo, move the robot, or clear memory. Preserve initial memory with your experiment.
Restart the five MCP servers once after installing this code update. Subsequent
run switches are picked up on every new tool call without restarting servers.
Stop issuing calls and wait for outstanding calls before changing experiments.
If a switch happens during a call, its end remains in the run where it started.
Without a marker, the first call creates one shared automatic run; a controlled
experiment must still explicitly start a fresh run. `CARS_LOG_DIR` optionally
overrides the log directory; set the same absolute path for every server and CLI.

All 18 currently registered tools across the five servers are instrumented:
camera observation, LiDAR radar/passage/laser/safety measurements, memory reads
and writes, pose/target/route calculations, autopilot motion, and map updates.
Each invocation writes `tool_start` and `tool_end` events with the same `call_id`
and `run_id`. `seq` is a process-safe, increasing event order within a run;
read the `tool_start` rows in this order to reconstruct call entry order.
Concurrent calls can overlap and complete in a different order. UTC timestamps,
server/tool names, all bound inputs, elapsed seconds, and outputs or exceptions
are retained. Sequence gaps can indicate an interrupted write. A start without
an end indicates an unfinished or interrupted call.

`tool_end.status=returned` only means the function returned: a returned sensor
error, safety stop, or timeout remains visible in `cikti_ozet` and is **not** a
success assertion. `status=exception` includes the exception type and message.
Text results are preserved without the old 300-character truncation. Images
are represented by MIME type, base64 length and SHA-256 of the base64 string;
the JSONL does not archive image pixels. These are tool traces, not model
reasoning traces; MCP requests rejected during schema validation before tool
entry are outside this logger. Logging failures propagate rather than silently
allowing an unrecorded invocation to begin. Filesystem locking uses Linux
`flock`, matching the supported Ubuntu/WSL runtime.

The durability harness starts a fresh run automatically and appends per-case
`test_result` events plus a `test_summary`. Its exit codes are 0 (all passed,
none skipped), 1 (failures), 2 (setup/import error), and 3 (incomplete/skipped).
Run the isolated logging and summary regressions without Gazebo:

```bash
python3 -m unittest discover -s tests -p test_experiment_log.py -v
```

## Repository structure

```text
.
|-- modules/
|   |-- experiment_log.py
|   |-- autopilot_mcp.py
|   |-- camera_mcp.py
|   |-- custom_memory_mcp.py
|   |-- kartograf_mcp.py
|   `-- lidar_mcp.py
|-- rules/
|   |-- CARS_MASTER_PROTOCOL.md
|   `-- CARS_SYSTEM_INSTRUCTION.md
|-- tests/
|   |-- README.md
|   |-- run_tests.sh
|   `-- test_dayaniklilik.py
|-- memory/                  # runtime-generated; ignored by Git
|-- logs/                    # runtime-generated; ignored by Git
|-- start_robot.sh
|-- requirements.txt
|-- .gitignore
`-- README.md
```

## Known limitations

- Simulation-only validation; no physical-robot experiments yet.
- No benchmark comparison with Nav2, SLAM, frontier exploration, or published vision-language navigation systems yet.
- Passage-width estimation is heuristic and environment dependent.
- Topological route planning currently uses unweighted breadth-first search.
- Runtime memory uses JSON files without transactional concurrency guarantees.
- Behavior remains sensitive to the selected language model and protocol interpretation.
- The current durability tests are an initial harness, not a complete verification suite.
- Controlled experiments require the explicit start command; images and model reasoning are not archived in JSONL.
- Dynamic obstacles, network latency, API cost, and sim-to-real transfer are not yet systematically evaluated.

## Planned evaluation

The first controlled evaluation will compare a reduced baseline with the full CARS protocol on:

1. direct unobstructed target approach;
2. target occluded by a table leg or similar obstacle;
3. passage or cross-room target navigation.

Planned metrics include success rate, collisions, false completion, path length, task time, oscillation count, tool/model calls, and human interventions.

## Citation

A technical preprint and citation metadata will be added after the first controlled experimental evaluation. Until then, results should be described as preliminary simulation evidence.

## License

No license has been selected yet. Until a license is added, the repository is publicly readable only if published; reuse and redistribution rights are not granted by default.
