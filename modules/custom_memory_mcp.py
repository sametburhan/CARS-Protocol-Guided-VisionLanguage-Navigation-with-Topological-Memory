from experiment_log import logged_tool
from fastmcp import FastMCP
import json
import os
from datetime import datetime
import time
import json

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
mcp = FastMCP("CognitiveMemory")

BASE_DIR = os.path.normpath(os.path.join(MODULE_DIR, "..", "memory"))
LTM_FILE = os.path.join(BASE_DIR, "long_term_memory.json")
STM_FILE = os.path.join(BASE_DIR, "short_term_memory.json")

os.makedirs(BASE_DIR, exist_ok=True)

# Initialize the layered LTM structure.
if not os.path.exists(LTM_FILE):
    with open(LTM_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "topological_graph": [], # Layer 2: Transitions between rooms or milestones.
            "content_tree": {}       # Layer 3: Objects and observation poses within rooms.
        }, f, indent=4)

if not os.path.exists(STM_FILE):
    with open(STM_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4)

# ==========================================
# RUNTIME FILE CHECKS AND HELPERS
# ==========================================
def ensure_files_exist():
    """Ensure that the memory directory and files exist before every operation."""
    os.makedirs(BASE_DIR, exist_ok=True)

    if not os.path.exists(LTM_FILE):
        with open(LTM_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "topological_graph": [],
                "content_tree": {}
            }, f, indent=4)

    if not os.path.exists(STM_FILE):
        with open(STM_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=4)

def load_json(file_path):
    ensure_files_exist() # Ensure storage exists before reading.
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(file_path, data):
    ensure_files_exist() # Ensure storage exists before writing.
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ==========================================
# LONG-TERM MEMORY: TOPOLOGY AND CONTENT TREE
# ==========================================
@mcp.tool()
@logged_tool("CognitiveMemory")
def save_long_term_memory(
    active_milestone_name: str, previous_milestone: str, transition_relation: str,
    object_name: str, object_x: float, object_y: float, robot_x: float, robot_y: float,
    robot_yaw: float, related_object: str, relation_description: str, properties: str
) -> str:
    """Save a milestone, topological connection, and optional object observation.

    For a new room, supply active_milestone_name and previous_milestone.
    To add only an object, use the string 'None' for previous_milestone and
    transition_relation. To add only a milestone, use 'None' for object_name.
    Use 'None' for absent related_object and relation_description values.
    Robot pose comes from get_robot_pose; object coordinates from analyze_target."""
    started = time.time()
    inputs = {"active_milestone_name": active_milestone_name, "object_name": object_name, "related_object": related_object}


    ltm = load_json(LTM_FILE)
    report = []

    # LAYER 2: TOPOLOGICAL CONNECTIONS
    if previous_milestone.lower() != "none":
        connection = {
            "source": previous_milestone,
            "target": active_milestone_name,
            "relation": transition_relation
        }
        if connection not in ltm["topological_graph"]:
            ltm["topological_graph"].append(connection)
            report.append(f"Topology updated: [{previous_milestone}] --{transition_relation}--> [{active_milestone_name}]")

    # LAYER 3: CREATE A CONTENT TREE BRANCH
    if active_milestone_name not in ltm["content_tree"]:
        ltm["content_tree"][active_milestone_name] = {
            "discovery_pose": {"x": robot_x, "y": robot_y, "yaw": robot_yaw},
            "objects": {}
        }
        report.append(f"New branch created: {active_milestone_name}")

    # ADD AN OBJECT TO THE BRANCH
    if object_name.lower() != "none":
        ltm["content_tree"][active_milestone_name]["objects"][object_name] = {
            "x": object_x,
            "y": object_y,
            "observation_pose": {"x": robot_x, "y": robot_y, "yaw": robot_yaw},  # Record the observer pose.
            "properties": properties,
            "related_object": related_object if related_object.lower() != "none" else None,   # Optional related object.
            "relation_description": relation_description if relation_description.lower() != "none" else None    # Optional relationship description.
            #"timestamp": datetime.now().strftime("%H:%M:%S")
        }
        report.append(f"Object added: '{object_name}' in the {active_milestone_name} branch.")

    save_json(LTM_FILE, ltm)
    result = " | ".join(report)
    return result

@mcp.tool()
@logged_tool("CognitiveMemory")
def read_long_term_memory() -> str:
    """Read the LTM topology and content tree with coordinates hidden."""
    ltm = load_json(LTM_FILE)

    # Hide coordinates to prevent unsupported agent-side coordinate calculations.
    if "content_tree" in ltm:
        for room_name, content in ltm["content_tree"].items():
            if "discovery_pose" in content:
                content["discovery_pose"] = "COORDINATES HIDDEN"
            if "objects" in content:
                for object_name, details in content["objects"].items():
                    # Remove coordinates only from the loaded copy; persisted JSON retains them.
                    if "x" in details: del details["x"]
                    if "y" in details: del details["y"]

                    # Hide observation-pose coordinates from the agent as well.
                    if "observation_pose" in details:
                        details["observation_pose"] = "COORDINATES HIDDEN"

    return json.dumps(ltm, indent=2, ensure_ascii=False)

# ==========================================
# SHORT-TERM MEMORY TOOLS
# ==========================================
@mcp.tool()
@logged_tool("CognitiveMemory")
def update_short_term_memory(last_action: str, observed_delta: str, local_state: str, intention: str) -> str:
    """Record the current action, observed delta, local state, and next intention.

    Update after every atomic step. Retain the latest six entries. Do not
    include global coordinates or long visual descriptions; use local motion
    feedback and a concise next-step intention."""
    started = time.time()
    from datetime import datetime

    # Create one entry for the current step.
    # Append it to the sliding history below.
    stm = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "last_action": last_action,
        "observed_delta": observed_delta,
        "local_state": local_state,
        "intention": intention
    }
    history = load_json(STM_FILE)
    if not isinstance(history, list):
        history = []
    history.append(stm)
    history = history[-6:]  # Keep only the six most recent entries.
    save_json(STM_FILE, history)
    result = (
        "STM updated. The last six steps are retained."
        "\n\n[SYSTEM REMINDER]: STM updated. You MUST review the STM window. "
        "If you detect repeated maneuvers or target loss, refer strictly to "
        "**Section 3: Stage 5 (Oscillation detection)** for the required "
        "procedure."
    )
    return result

@mcp.tool()
@logged_tool("CognitiveMemory")
def read_short_term_memory() -> str:
    """Return the latest six STM entries, ordered from oldest to newest."""
    return json.dumps(load_json(STM_FILE), indent=2, ensure_ascii=False)

@mcp.tool()
@logged_tool("CognitiveMemory")
def record_beyond_passage_observation(passage_name: str, object_name: str, estimated_properties: str) -> str:
    """Record a prominent object seen beyond a passage in an unvisited room.

    Store only its name and estimated properties, without coordinates. These
    preliminary notes live outside content_tree and are never read by
    calculate_object_heading or calculate_observation_heading. Use them as
    Stage 0 planning hints, then explicitly save a verified LTM observation
    after physically entering the room."""
    started = time.time()
    ltm = load_json(LTM_FILE)
    if "beyond_passage_observations" not in ltm:
        ltm["beyond_passage_observations"] = {}
    if passage_name not in ltm["beyond_passage_observations"]:
        ltm["beyond_passage_observations"][passage_name] = []
    ltm["beyond_passage_observations"][passage_name].append({
        "object_name": object_name,
        "estimated_properties": estimated_properties
    })
    save_json(LTM_FILE, ltm)
    result = f"Preliminary observation saved: '{object_name}' is visible beyond '{passage_name}'."
    return result

@mcp.tool()
@logged_tool("CognitiveMemory")
def start_cognitive_stage(stage: int, provider: str, model: str) -> str:
    """Start an observed Stage 0-5 interval for an instrumented experiment.

    Supply the provider and exact model selected by the operator, never guess.
    Call immediately before the stage and retain the returned stage_id.
    Measures total stage wall time, NOT API latency or model reasoning time.
    """
    from stage_metrics import start_stage
    return start_stage(stage, provider, model)


@mcp.tool()
@logged_tool("CognitiveMemory")
def finish_cognitive_stage(stage_id: str, outcome: str) -> str:
    """End an observed stage using its returned ID, including after run rotation.

    Outcome must be completed, failed, or cancelled. Do not supply elapsed
    times or token estimates: timing is measured by the server.
    """
    from stage_metrics import finish_stage
    return json.dumps(finish_stage(stage_id, outcome), ensure_ascii=True)


if __name__ == "__main__":
    mcp.run()
