from experiment_log import logged_tool
from fastmcp import FastMCP
import roslibpy
import time
import math
import os
import json

mcp = FastMCP("Autopilot")

# Global state
ros_client = None
latest_scan_ranges = None
cmd_vel_pub = None
front_distance = 3.5
current_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}

# Sensor freshness tracking
last_lidar_update = None
last_odom_update = None
MAX_SENSOR_AGE_SECONDS = 1.0  # Older readings are considered stale.

# Motion limits are module constants; production values remain unchanged.
# Durability tests can temporarily reduce these values.
MAX_TURN_DEGREES = 60.0
MAX_DISTANCE_METERS = 3.5
FRONT_STOP_THRESHOLD_METERS = 0.25
SAFETY_RADIUS_METERS = 0.20
MAX_TURN_DURATION_SECONDS = 10.0
MAX_MOVE_DURATION_SECONDS = 25.0

# Shared long-term memory file
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
LTM_FILE = os.path.normpath(os.path.join(MODULE_DIR, "..", "memory", "long_term_memory.json"))


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def stop_robot():
    global cmd_vel_pub
    if cmd_vel_pub is not None:
        msg = roslibpy.Message({'linear': {'x': 0.0, 'y': 0.0, 'z': 0.0}, 'angular': {'x': 0.0, 'y': 0.0, 'z': 0.0}})
        cmd_vel_pub.publish(msg)

def _check_safety_zone(radius=0.20):
    """Check for obstacles within the full 360-degree safety radius.

    Shared by scan_safety_zone and the motion loop to keep checks consistent.
    Returns (obstacle_detected: bool, nearby_obstacles: list)."""
    global latest_scan_ranges
    if latest_scan_ranges is None:
        return False, []
    num_beams = len(latest_scan_ranges)
    if num_beams == 0:
        return False, []

    nearby_obstacles = []
    for idx, r in enumerate(latest_scan_ranges):
        if r is not None and 0.05 < r < radius:
            angle_degrees = (idx / num_beams) * 360.0
            local_angle = angle_degrees - 360.0 if angle_degrees > 180 else angle_degrees
            nearby_obstacles.append({"angle": local_angle, "distance": r})

    return (len(nearby_obstacles) > 0), nearby_obstacles

def _check_motion_preconditions():
    """Check ROS connectivity and the presence and freshness of sensor data.

    Returns (allowed: bool, error_message: str)."""
    global ros_client, latest_scan_ranges, last_lidar_update, last_odom_update

    if ros_client is None or not ros_client.is_connected:
        return False, "MOTION BLOCKED: No ROS connection. No motor command was sent."

    if latest_scan_ranges is None:
        return False, "MOTION BLOCKED: No Lidar data has been received. Motion canceled for safety."

    if last_odom_update is None:
        return False, "MOTION BLOCKED: No odometry data has been received. Pose unknown; motion canceled."

    now = time.time()
    if (now - last_lidar_update) > MAX_SENSOR_AGE_SECONDS:
        return False, f"MOTION BLOCKED: Lidar data is {now - last_lidar_update:.1f}s old (stale). Motion canceled."

    if (now - last_odom_update) > MAX_SENSOR_AGE_SECONDS:
        return False, f"MOTION BLOCKED: Odometry data is {now - last_odom_update:.1f}s old (stale). Motion canceled."

    return True, ""

def normalize_angle(angle):
    """Normalize an angle to the range -180 to 180 degrees."""
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    return angle

# ==========================================
# ROS 2 CONNECTIONS (Lidar and odometry)
# ==========================================
def lidar_callback(message):
    global front_distance, latest_scan_ranges, last_lidar_update
    if 'ranges' in message:
        last_lidar_update = time.time()  # Record the sensor update time.
        ranges = message['ranges']
        latest_scan_ranges = message['ranges']
        num_beams = len(ranges)
        degrees_to_check = 15
        beams_to_check = int(degrees_to_check * (num_beams / 360.0))

        front_ranges = []
        for i in range(0, beams_to_check):
            r = ranges[i]
            if r is not None and not math.isinf(r) and not math.isnan(r) and r > 0.15:
                front_ranges.append(r)
        for i in range(num_beams - beams_to_check, num_beams):
            r = ranges[i]
            if r is not None and not math.isinf(r) and not math.isnan(r) and r > 0.15:
                front_ranges.append(r)

        if front_ranges:
            front_distance = min(front_ranges)
        else:
            front_distance = 3.5

def odom_callback(message):
    """Read the robot position and heading from Gazebo odometry."""
    global current_pose, last_odom_update
    try:
        pos = message['pose']['pose']['position']
        ori = message['pose']['pose']['orientation']
        current_pose["x"] = pos["x"]
        current_pose["y"] = pos["y"]

        siny_cosp = 2 * (ori["w"] * ori["z"] + ori["x"] * ori["y"])
        cosy_cosp = 1 - 2 * (ori["y"]**2 + ori["z"]**2)
        yaw_rad = math.atan2(siny_cosp, cosy_cosp)
        current_pose["yaw"] = math.degrees(yaw_rad)

        last_odom_update = time.time()  # Update only after successful parsing.
    except KeyError:
        pass

try:
    ros_client = roslibpy.Ros(host='127.0.0.1', port=9090)
    ros_client.run() 
    
    cmd_vel_pub = roslibpy.Topic(ros_client, '/cmd_vel', 'geometry_msgs/Twist')
    
    scan_topic = roslibpy.Topic(ros_client, '/scan', 'sensor_msgs/LaserScan')
    scan_topic.subscribe(lidar_callback)

    odom_topic = roslibpy.Topic(ros_client, '/odom', 'nav_msgs/Odometry')
    odom_topic.subscribe(odom_callback)
    
    print("Autopilot MCP server: /cmd_vel, /scan and /odom are active.")
except Exception as e:
    print(f"ROS connection error: {e}")

# ==========================================
# MCP TOOLS FOR THE AGENT
# ==========================================
@mcp.tool()
@logged_tool("Autopilot")
def calculate_object_heading(room_name: str, object_name: str) -> str:
    """Calculate the estimated direction and distance to an object stored in LTM.

    Returns the turn angle to send to move_robot, relative to the current pose.
    Use only for an object in the current room; plan a route first otherwise."""
    import os, json
    
    if not os.path.exists(LTM_FILE):
        return "ERROR: LTM file not found."
        
    with open(LTM_FILE, "r", encoding="utf-8") as f:
        ltm = json.load(f)
        
    try:
        object_data = ltm["content_tree"][room_name]["objects"][object_name]
        target_x = object_data["x"]
        target_y = object_data["y"]
    except KeyError:
        return f"ERROR: Object '{object_name}' was not found in '{room_name}'."
        
    global current_pose
    dx = target_x - current_pose['x']
    dy = target_y - current_pose['y']
    
    distance = math.sqrt(dx**2 + dy**2)
    global_target_angle_rad = math.atan2(dy, dx)
    global_target_angle_deg = math.degrees(global_target_angle_rad)
    
    # Calculate the local angle.
    difference_deg = global_target_angle_deg - current_pose['yaw']
    
    # Normalize the angle to the range -180 to 180 degrees.
    while difference_deg > 180.0: difference_deg -= 360.0
    while difference_deg < -180.0: difference_deg += 360.0
    
    direction_label = "LEFT" if difference_deg > 0 else "RIGHT"
    
    return (
        f"COGNITIVE COMPASS RESULT:\n"
        f"- Target: {object_name} ({room_name})\n"
        f"- Estimated distance: {distance:.2f} meters\n"
        f"- Direction: To your {direction_label}.\n"
        f"USAGE: Call move_robot with target_angle_degrees={difference_deg:.1f}."
    )

@mcp.tool()
@logged_tool("Autopilot")
def get_robot_pose() -> str:
    """Return the current global robot pose (x, y, yaw in degrees).

    Use this tool before recording a room, object observation, or starting point
    in LTM instead of inventing coordinates."""
    return f"Current pose -> X: {current_pose['x']:.2f}, Y: {current_pose['y']:.2f}, Yaw: {current_pose['yaw']:.2f} degrees"

@mcp.tool()
@logged_tool("Autopilot")
def calculate_observation_heading(room_name: str, object_name: str) -> str:
    """Calculate the heading and distance to an object's recorded observation pose.

    Use when the object cannot be found at its stored location. Returns motion
    guidance rather than raw coordinates. Plan a route first if in another room."""
    import os, json

    if not os.path.exists(LTM_FILE):
        return "ERROR: LTM file not found."

    with open(LTM_FILE, "r", encoding="utf-8") as f:
        ltm = json.load(f)

    try:
        target = ltm["content_tree"][room_name]["objects"][object_name]["observation_pose"]
    except KeyError:
        return f"ERROR: No observation pose was found for '{object_name}' in '{room_name}'."

    global current_pose
    dx = target["x"] - current_pose['x']
    dy = target["y"] - current_pose['y']

    distance = math.sqrt(dx**2 + dy**2)
    global_target_angle_deg = math.degrees(math.atan2(dy, dx))

    # current_pose['yaw'] is already in degrees; do not convert it a second time.
    difference_deg = global_target_angle_deg - current_pose['yaw']
    difference_deg = normalize_angle(difference_deg)  # Use the shared angle normalization helper.

    return (
        f"PREVIOUS OBSERVATION COMPASS:\n"
        f"- Target: The recorded observation point for '{object_name}' ({room_name})\n"
        f"- Estimated distance: {distance:.2f} meters\n"
        f"USAGE: Call move_robot with target_angle_degrees={difference_deg:.1f}."
    )

@mcp.tool()
@logged_tool("Autopilot")
def analyze_target(lidar_angle_degrees: float, estimated_distance_meters: float) -> str:
    """Convert a target's yellow Lidar angle (0-360) to a signed autopilot turn.

    Also estimate global X/Y coordinates for LTM using the supplied measured
    distance. These coordinates are sensor-based estimates, not ground truth."""
    global current_pose
    
    if lidar_angle_degrees > 180:
        turn_command = lidar_angle_degrees - 360.0
    else:
        turn_command = lidar_angle_degrees
        
    yaw_rad = math.radians(current_pose['yaw'])
    delta_rad = math.radians(turn_command)
    absolute_angle_rad = yaw_rad + delta_rad
    
    target_x = current_pose['x'] + (estimated_distance_meters * math.cos(absolute_angle_rad))
    target_y = current_pose['y'] + (estimated_distance_meters * math.sin(absolute_angle_rad))
    
    report = (
        f"TARGET ANALYSIS COMPLETE:\n"
        f"- Autopilot turn command: {turn_command:.1f} degrees\n"
        f"- Estimated global coordinates (for LTM) -> X: {target_x:.2f}, Y: {target_y:.2f}\n"
        f"[WARNING]: These coordinates are sensor-based estimates, not exact values. "
        f"Record this uncertainty in the LTM 'properties' field.\n"
        f"USAGE: Call move_robot with target_angle_degrees={turn_command:.1f}."
    )
    return report

@mcp.tool()
@logged_tool("Autopilot")
def scan_safety_zone() -> str:
    """Scan a 360-degree safety zone with a radius of 20 cm (0.2 meters).

    Check for nearby obstacles before motion and during approach planning."""
    global latest_scan_ranges
    if latest_scan_ranges is None:
        return "ERROR: Lidar (radar) data is unavailable."

    obstacle_detected, nearby_obstacles = _check_safety_zone(radius=0.20)

    if not obstacle_detected:
        return "SAFETY ZONE CLEAR: No obstacles detected within a 20 cm radius. Route clear."

    report = "SAFETY WARNING: Nearby obstacles detected!\n\n"
    for obstacle in nearby_obstacles:
        angle = obstacle["angle"]
        distance_cm = obstacle["distance"] * 100
        if -30 <= angle <= 30:
            direction = "FRONT (blocking the forward route)"
        elif 30 < angle <= 150:
            direction = f"LEFT (angle: +{angle:.1f} degrees)"
        elif -150 <= angle < -30:
            direction = f"RIGHT (angle: {angle:.1f} degrees)"
        else:
            direction = "REAR"
        report += f"- Position: {direction} | Distance: {distance_cm:.1f} cm\n"

    report += "\n[MANEUVER]: If these obstacles block the target direction, do not drive straight ahead. Turn at least 30 degrees away from the obstacle and advance 0.3 meters along a verified safe waypoint to bypass it."
    return report

def _near_cluster_angular_width(
    scan,
    target_idx,
    num_beams,
    hit_distance,
    tolerance=0.08,
    max_window_deg=20,
):
    """Calculate the angular width occupied by beams near hit_distance.

    Beams within ±tolerance meters of hit_distance are counted inside a
    ±max_window_deg search window centered on target_idx.
    """
    if num_beams <= 0:
        return 0.0

    window_idx = int(max_window_deg / 360.0 * num_beams)
    consistent_beams = 0

    for offset in range(-window_idx, window_idx + 1):
        idx = (target_idx + offset) % num_beams
        distance = scan[idx]

        if (
            distance is not None
            and math.isfinite(distance)
            and abs(distance - hit_distance) <= tolerance
        ):
            consistent_beams += 1

    return consistent_beams * (360.0 / num_beams)


def _narrow_cluster_note(cluster_width_deg):
    """Return an objective structural note for a narrow Lidar cluster."""
    if cluster_width_deg is None or cluster_width_deg >= 6.0:
        return ""

    return (
        "\n"
        f"Note: This distance reading spans a narrow angular cluster of only "
        f"{cluster_width_deg:.1f} degrees. Structurally, this profile matches "
        f"a thin obstacle (e.g., pole or furniture leg) rather than a wide "
        f"object surface."
        "\n\n[SYSTEM REMINDER]: Structurally, this profile matches a thin "
        "obstacle. Please review **Section 6 (Occlusion prohibitions)** and "
        "**Section 3: Final target verification** to ensure you are not aiming "
        "at a foreground obstacle."
    )


def _near_distance_disagreement_reminder(distance_meters):
    """Prompt visual/Lidar reconciliation only for a very near reading."""
    if distance_meters >= 0.5:
        return ""

    return (
        "\n\n[SYSTEM REMINDER]: If this near distance contradicts your clear "
        "visual evidence, review **Section 3: Spatial awareness synthesis "
        "(Disagreement & Ghost Object Exception)** to resolve the conflict."
    )


@mcp.tool()
@logged_tool("Autopilot")
def measure_laser_distance(target_angle: float) -> str:
    """Measure the nearest obstacle in a 30-degree cone around target_angle.

    Returns its measured distance and angle. It also measures the angular
    width of nearby Lidar beams at approximately the same distance. A narrow
    cluster is reported as an objective structural observation.

    Use this tool instead of estimating distance from the radar image.
    The forward shortcut uses front_distance.
    """
    global latest_scan_ranges, front_distance

    # Take local snapshots so one invocation uses a consistent set of values
    # even if the ROS callback replaces the global scan during processing.
    scan = latest_scan_ranges
    live_front_distance = front_distance

    # For a target already aligned with the camera center, preserve the
    # existing front_distance shortcut.
    if abs(target_angle) <= 2.0:
        if live_front_distance >= 3.5:
            return (
                "Target angle (0 degrees): No obstacle detected in the "
                "+/-15-degree cone. Route clear (front_distance)."
            )

        cluster_width_deg = None

        # Locate the beam which produced front_distance so the cluster is
        # measured around the actual hit rather than around beam zero.
        if scan:
            num_beams = len(scan)
            beams_to_check = int(15.0 / 360.0 * num_beams)

            front_indices = list(range(0, beams_to_check))
            front_indices.extend(
                range(num_beams - beams_to_check, num_beams)
            )

            valid_front_indices = [
                idx
                for idx in front_indices
                if (
                    scan[idx] is not None
                    and math.isfinite(scan[idx])
                    and scan[idx] > 0.15
                )
            ]

            if valid_front_indices:
                measured_idx = min(
                    valid_front_indices,
                    key=lambda idx: abs(scan[idx] - live_front_distance),
                )

                cluster_width_deg = _near_cluster_angular_width(
                    scan=scan,
                    target_idx=measured_idx,
                    num_beams=num_beams,
                    hit_distance=live_front_distance,
                )

        report = (
            "LASER MEASUREMENT SUCCESSFUL (live front_distance)!\n"
            f"- Estimated angle: {target_angle} degrees\n"
            "- Measured angle: 0.0 degrees (front, corresponds to 0.0 degrees on the 360-degree radar map)\n"
            f"- Measured object distance: {live_front_distance:.2f} meters\n"
            "(Use 0.0 degrees when directing the autopilot.)"
        )

        report += _near_distance_disagreement_reminder(live_front_distance)
        return report + _narrow_cluster_note(cluster_width_deg)

    if scan is None:
        return "ERROR: Lidar (radar) data is unavailable."

    num_beams = len(scan)
    if num_beams == 0:
        return "ERROR: Lidar data is empty."

    normalized_target_angle = target_angle % 360.0
    target_idx = int(normalized_target_angle / 360.0 * num_beams)

    window_deg = 15
    window_idx = int(window_deg / 360.0 * num_beams)

    measured_distance = float("inf")
    measured_idx = None
    precise_angle = target_angle

    for offset in range(-window_idx, window_idx + 1):
        idx = (target_idx + offset) % num_beams
        distance = scan[idx]

        if (
            distance is not None
            and math.isfinite(distance)
            and 0.1 < distance < 12.0
            and distance < measured_distance
        ):
            measured_distance = distance
            measured_idx = idx

            computed_angle = idx / num_beams * 360.0
            precise_angle = (
                computed_angle - 360.0
                if computed_angle > 180.0
                else computed_angle
            )

    if measured_idx is None:
        return (
            f"Target angle ({target_angle} degrees): No obstacle detected "
            f"in the +/-{window_deg}-degree cone. Route clear."
        )

    cluster_width_deg = _near_cluster_angular_width(
        scan=scan,
        target_idx=measured_idx,
        num_beams=num_beams,
        hit_distance=measured_distance,
    )

    radar_angle = (precise_angle + 360.0) % 360.0
    
    report = (
        "LASER MEASUREMENT SUCCESSFUL!\n"
        f"- Estimated angle: {target_angle} degrees\n"
        f"- Measured angle: {precise_angle:.1f} degrees (corresponds to {radar_angle:.1f} degrees on the 360-degree radar map)\n"
        f"- Measured object distance: {measured_distance:.2f} meters\n"
        f"(Use the signed measured angle of {precise_angle:.1f} degrees when "
        "directing the autopilot.)"
    )

    report += _near_distance_disagreement_reminder(measured_distance)
    return report + _narrow_cluster_note(cluster_width_deg)

@mcp.tool()
@logged_tool("Autopilot")
def plan_topological_route(start_milestone: str, target_milestone: str) -> str:
    """Find a shortest room-and-passage route using the LTM topology and BFS.

    Follow these milestones before approaching an object in another room,
    rather than using a direct heading that might cross a wall."""
    import os, json
    from collections import deque
    
    if not os.path.exists(LTM_FILE):
        return "ERROR: LTM file not found."
        
    with open(LTM_FILE, "r", encoding="utf-8") as f:
        ltm = json.load(f)
        
    topology = ltm.get("topological_graph", [])
    
    # Build a graph assuming bidirectional transitions.
    graph = {}
    for connection in topology:
        k = connection["source"]
        h = connection["target"]
        if k not in graph: graph[k] = []
        if h not in graph: graph[h] = []
        graph[k].append(h)
        graph[h].append(k) # Include the return path.
        
    if start_milestone not in graph or target_milestone not in graph:
        return f"ERROR: {start_milestone} or {target_milestone} was not found in the LTM topology."

    # Find a shortest route using breadth-first search.
    queue = deque([[start_milestone]])
    visited = set([start_milestone])
    
    while queue:
        route = queue.popleft()
        current_node = route[-1]
        
        if current_node == target_milestone:
            # Format the route as human-readable guidance.
            route_text = " -> ".join(route)
            report = (
                f"ROUTE PLAN CREATED:\n"
                f"Follow these milestones in order to reach the target through known transitions:\n"
                f"[ROUTE]: {route_text}\n\n"
                f"INSTRUCTION: Visit each milestone in order before taking a direct heading to the final target."
            )
            return report
            
        for neighbor in graph.get(current_node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                new_route = list(route)
                new_route.append(neighbor)
                queue.append(new_route)
                
    return "ERROR: No known route was found between these two points."

@mcp.tool()
@logged_tool("Autopilot")
def move_robot(target_angle_degrees: float, target_distance_meters: float) -> str:
    """Execute a bounded turn followed by a bounded translation.

    Maximum turn: 60 degrees. Maximum distance: 3.5 meters per call.
    Motion requires connected ROS and present, fresh Lidar and odometry data.
    Both motion loops have timeouts. Sensor loss stops the motors. Translation
    continuously checks frontal clearance and the full near-field safety zone.
    Positive angles turn left; negative angles turn right. Negative distance reverses."""
    call_started = time.time()

    target_angle_degrees = normalize_angle(target_angle_degrees)

    input_summary = {"target_angle_degrees": target_angle_degrees, "target_distance_meters": target_distance_meters}

    # CHECK MOTION PRECONDITIONS
    allowed, error_message = _check_motion_preconditions()
    if not allowed:
        return error_message

    applied_angle = min(abs(target_angle_degrees), MAX_TURN_DEGREES)
    if target_angle_degrees < 0:
        applied_angle = -applied_angle

    applied_distance = min(abs(target_distance_meters), MAX_DISTANCE_METERS)
    if target_distance_meters < 0:
        applied_distance = -applied_distance

    global cmd_vel_pub, current_pose, front_distance

    initial_yaw = current_pose['yaw']
    turn_timeout = False
    sensor_lost = False

    if abs(applied_angle) > 0:
        target_absolute_yaw = normalize_angle(current_pose['yaw'] + applied_angle)
        tolerance_degrees = 1.5
        turn_started = time.time()
        while True:
            if time.time() - turn_started > MAX_TURN_DURATION_SECONDS:
                turn_timeout = True
                break

            allowed, error_message = _check_motion_preconditions()
            if not allowed:
                sensor_lost = True
                break

            difference = normalize_angle(target_absolute_yaw - current_pose['yaw'])
            if abs(difference) <= tolerance_degrees:
                break
            angular_speed = 0.4 if difference > 0 else -0.4
            if abs(difference) < 15.0:
                angular_speed = 0.15 if difference > 0 else -0.15
            twist_msg = roslibpy.Message({
                'linear': {'x': 0.0, 'y': 0.0, 'z': 0.0},
                'angular': {'x': 0.0, 'y': 0.0, 'z': angular_speed}
            })
            cmd_vel_pub.publish(twist_msg)
            time.sleep(0.05)
        stop_robot()
        time.sleep(0.5)

    if sensor_lost:
        report = f"SENSOR/CONNECTION LOST: During rotation: {error_message} Motors stopped.\n"
        return report

    measured_turn = normalize_angle(current_pose['yaw'] - initial_yaw)

    # ==========================================
    # DISTANCE CONTROL
    # ==========================================
    stopped_early = False
    move_timeout = False
    sensor_lost_during_move = False
    traveled_distance = 0.0

    if abs(applied_distance) > 0:
        initial_x = current_pose['x']
        initial_y = current_pose['y']
        tolerance_meters = 0.02
        move_started = time.time()

        while True:
            if time.time() - move_started > MAX_MOVE_DURATION_SECONDS:
                move_timeout = True
                break

            allowed, error_message = _check_motion_preconditions()
            if not allowed:
                sensor_lost_during_move = True
                break

            traveled_distance = math.sqrt((current_pose['x'] - initial_x)**2 + (current_pose['y'] - initial_y)**2)
            remaining_distance = abs(applied_distance) - traveled_distance

            if remaining_distance <= tolerance_meters:
                break

            if applied_distance > 0 and front_distance < FRONT_STOP_THRESHOLD_METERS:
                stopped_early = True
                break

            obstacle_detected, nearby_obstacles = _check_safety_zone(radius=SAFETY_RADIUS_METERS)
            if obstacle_detected:
                collision_risk = False
                for obstacle in nearby_obstacles:
                    angle = obstacle["angle"]
                    if applied_distance > 0 and (-90 <= angle <= 90):
                        collision_risk = True
                        break
                    elif applied_distance < 0 and (angle < -90 or angle > 90):
                        collision_risk = True
                        break
                if collision_risk:
                    stopped_early = True
                    break

            linear_speed = 0.2 if applied_distance > 0 else -0.2
            if remaining_distance < 0.15:
                linear_speed = 0.08 if applied_distance > 0 else -0.08

            twist_msg = roslibpy.Message({
                'linear': {'x': linear_speed, 'y': 0.0, 'z': 0.0},
                'angular': {'x': 0.0, 'y': 0.0, 'z': 0.0}
            })
            cmd_vel_pub.publish(twist_msg)
            time.sleep(0.05)

        stop_robot()
        time.sleep(0.5)
        traveled_distance = math.sqrt((current_pose['x'] - initial_x)**2 + (current_pose['y'] - initial_y)**2)

    # ==========================================
    # MOTION FEEDBACK
    # ==========================================
    report = "MOTION SEQUENCE FINISHED AND MOTORS STOPPED.\n"

    if abs(target_angle_degrees) > MAX_TURN_DEGREES:
        remaining_angle = target_angle_degrees - applied_angle
        report += f"TURN LIMIT: Requested {target_angle_degrees} degrees; safety limit applied {applied_angle} degrees. Measured: {measured_turn:.1f} degrees. (Remaining: {remaining_angle} degrees)\n"
    elif turn_timeout:
        report += f"TURN TIMEOUT: Target angle not reached within {MAX_TURN_DURATION_SECONDS}s. Measured turn: {measured_turn:.1f} degrees (requested: {applied_angle} degrees). The robot may be physically stuck.\n"
    else:
        report += f"- Turn: Requested {target_angle_degrees} degrees (Measured: {measured_turn:.1f} degrees) completed successfully.\n"

    if sensor_lost_during_move:
        report += f"SENSOR/CONNECTION LOST: Sensor/ROS connection lost during translation; motors stopped. Traveled: {traveled_distance:.2f}m (requested: {target_distance_meters}m).\n"
    elif move_timeout:
        report += f"DISTANCE TIMEOUT: Target distance not reached within {MAX_MOVE_DURATION_SECONDS}s. Traveled: {traveled_distance:.2f}m (requested: {target_distance_meters}m). The robot may be physically stuck.\n"
    elif stopped_early:
        report += f"LIVE OBSTACLE DETECTED: Traveled only {traveled_distance:.2f}m; an obstacle was detected during motion and the robot stopped. (Target: {target_distance_meters}m)\n"
    elif abs(target_distance_meters) > MAX_DISTANCE_METERS:
        remaining_distance = target_distance_meters - applied_distance
        report += f"DISTANCE LIMIT: Requested {target_distance_meters}m; safety limit applied {applied_distance}m. Traveled: {traveled_distance:.2f}m. (Remaining: {remaining_distance}m)\n"
    else:
        report += f"- Translation: Requested {target_distance_meters}m (Measured: {traveled_distance:.2f}m) completed successfully.\n"

    report += "\n[REQUIRED]: The robot is stopped. Update STM in Stage 5, then return to Stage 2 and inspect the camera and radar from the new pose. If the route is clear, call the autopilot again with the remaining values."

    if stopped_early:
        report += (
            "\n\n[SYSTEM REMINDER]: A live obstacle blocked the path. Do not use "
            "bash commands or attempt to read code parameters. Please review "
            "**Section 3: Stage 4** and **Section 3: Spatial awareness "
            "synthesis** regarding obstacle handling and limits."
        )

    if (
        abs(target_angle_degrees) > MAX_TURN_DEGREES
        or abs(target_distance_meters) > MAX_DISTANCE_METERS
    ):
        report += (
            "\n\n[SYSTEM REMINDER]: A motion safety limit was applied. Please "
            "review **Section 6, Rule 4** and **Section 3: Stage 4** before "
            "making your next movement decision."
        )

    return report


if __name__ == "__main__":
    mcp.run()
