from experiment_log import logged_tool
from fastmcp import FastMCP
import mcp.types as types # Standard MCP content types.
import base64             # Image encoding.
import roslibpy
import cv2
import numpy as np
from PIL import Image as PILImage
import io
import math
import time
import os
import json

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

# Create the MCP server.
mcp = FastMCP("LidarRadar")

# Global sensor state.
latest_scan_ranges = None
last_lidar_update = None
MAX_SENSOR_AGE_SECONDS = 1.0


# ==========================================
# ROS 2 CONNECTION THROUGH ROSBRIDGE
# ==========================================
def lidar_callback(message):
    global latest_scan_ranges, last_lidar_update
    if 'ranges' in message:
        latest_scan_ranges = message['ranges']
        last_lidar_update = time.time()

def _ensure_ros_connection():          # Reconnect when needed.
    """Try to reconnect if the ROS connection has been lost."""
    global ros_client
    if not ros_client.is_connected:
        print("Lidar MCP: ROS disconnected. Reconnecting...")
        try:
            ros_client.run()
            time.sleep(1.0)
        except Exception as e:
            print(f"Reconnection failed: {e}")

def _is_lidar_fresh():                 # Sensor freshness check.
    global last_lidar_update
    if last_lidar_update is None:
        return False
    return (time.time() - last_lidar_update) <= MAX_SENSOR_AGE_SECONDS

# Connect to rosbridge on port 9090.
try:
    ros_client = roslibpy.Ros(host='127.0.0.1', port=9090)
    scan_topic = roslibpy.Topic(ros_client, '/scan', 'sensor_msgs/LaserScan')
    scan_topic.subscribe(lidar_callback)
    ros_client.run()
    print("Lidar MCP server: Connected to ROS bridge and listening to /scan.")
except Exception as e:
    print(f"ROS connection error: {e}")

# ==========================================
# IMAGE PROCESSING
# ==========================================
def _encode_image_to_imagecontent(image):
    # Reduce resolution and JPEG quality for faster transfer.
    img_copy = image.copy()
    max_size = (320, 240)
    img_copy.thumbnail(max_size)
    buffer = io.BytesIO()
    img_copy.save(buffer, format="JPEG", quality=40, optimize=True)

    # Encode the image as Base64 text.
    base64_data = base64.b64encode(buffer.getvalue()).decode('utf-8')

    # Return the standard MCP image content type.
    return types.ImageContent(
        type="image",
        data=base64_data,
        mimeType="image/jpeg"
    )

def generate_lidar_radar_visual_pil(scan_data, max_range=3.5, img_size=320):
    img = np.zeros((img_size, img_size, 3), dtype=np.uint8)
    center = img_size // 2
    scale = (center - 25) / max_range

    num_beams = len(scan_data)
    angle_min = 0.0
    angle_increment = (2.0 * 3.14159265359) / num_beams

    font = cv2.FONT_HERSHEY_SIMPLEX

    # Draw reference grid lines every 30 degrees.
    grid_angles_deg = range(0, 360, 30)

    # Draw faint background grid lines.
    for angle_deg in grid_angles_deg:
        rad = math.radians(angle_deg)
        x_end = int(center - (max_range * scale * math.sin(rad)))
        y_end = int(center - (max_range * scale * math.cos(rad)))
        cv2.line(img, (center, center), (x_end, y_end), (40, 40, 40), 1) # Dark gray.

    # Track the closest obstacle at each grid angle.
    # Initially treat each direction as beyond the maximum range.
    closest_obstacles = {deg: max_range + 1 for deg in grid_angles_deg}
    tolerance_deg = 2.0 # Accept readings within 2 degrees of a grid direction.

    # Draw Lidar obstacles and find grid intersections.
    for i, r in enumerate(scan_data):
        if r is None or math.isinf(r) or math.isnan(r) or r < 0.1:
            continue

        dist = min(r, max_range)
        angle_rad = angle_min + (i * angle_increment)
        angle_deg_current = math.degrees(angle_rad) % 360

        # ROS X points forward and Y points left; convert to image coordinates.
        x_m = dist * math.cos(angle_rad)
        y_m = dist * math.sin(angle_rad)

        px = int(center - (y_m * scale))
        py = int(center - (x_m * scale))

        # Draw each obstacle as a white point.
        cv2.circle(img, (px, py), 2, (255, 255, 255), -1)

        # Check whether the obstacle is near a grid direction.
        for grid_deg in grid_angles_deg:
            # Use the circular angle difference: 359 and 1 degrees differ by 2.
            diff = abs(angle_deg_current - grid_deg)
            if diff > 180:
                diff = 360 - diff

            # Keep the nearest obstacle close to the reference line.
            if diff <= tolerance_deg:
                if dist < closest_obstacles[grid_deg]:
                    closest_obstacles[grid_deg] = dist

    # Add adaptive angle labels.
    for grid_deg, dist in closest_obstacles.items():
        if dist <= max_range: # Only label directions with a detected obstacle.
            rad = math.radians(grid_deg)
            # Offset the label from the obstacle to avoid clipping.
            text_dist = dist - 0.3 if dist > 0.6 else dist + 0.3
            tx = int(center - (text_dist * scale * math.sin(rad)))
            ty = int(center - (text_dist * scale * math.cos(rad)))

            # Draw the angle number in yellow, such as 30, 60, or 330.
            cv2.putText(img, str(grid_deg), (tx - 10, ty + 5), font, 0.4, (0, 255, 255), 1)

    # Draw a red forward arrow.
    arrow_length = 40
    cv2.arrowedLine(img, (center, center), (center, center - arrow_length), (0, 0, 255), 2, tipLength=0.3)

    # Draw the robot as a green circle at the center.
    cv2.circle(img, (center, center), 5, (0, 255, 0), -1)

    # Add direction labels (FRONT, REAR, LEFT, RIGHT).
    for label, side in (("FRONT", "top"), ("REAR", "bottom"),
                        ("LEFT", "left"), ("RIGHT", "right")):
        (label_width, _), _ = cv2.getTextSize(label, font, 0.5, 1)
        if side in ("top", "bottom"):
            label_x = center - label_width // 2
            label_y = 15 if side == "top" else img_size - 5
        else:
            label_x = 5 if side == "left" else img_size - label_width - 5
            label_y = center + 5
        cv2.putText(img, label, (label_x, label_y), font, 0.5, (0, 255, 255), 1)

    rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return PILImage.fromarray(rgb_img)

# ==========================================
# MCP TOOLS FOR THE AGENT
# ==========================================
@mcp.tool()
@logged_tool("LidarRadar")
def get_lidar_radar_map():
    """Get a fresh 360-degree Lidar radar image.

    Use it to inspect rear and side blind spots and obstacle geometry. White
    points mark obstacles on a black background; the green circle is the robot.
    Use measurement tools for numeric distances rather than image estimates."""
    started = time.time()
    global latest_scan_ranges
    _ensure_ros_connection()

    if not ros_client.is_connected:          # Require a live connection.
        result = "ERROR: No ROS connection; radar data unavailable."
        return result

    # Discard the old scan so every request waits for fresh Lidar data.
    latest_scan_ranges = None

    timeout = 10.0
    start = time.time()

    # Wait for the first new ROS scan.
    while latest_scan_ranges is None:
        if (time.time() - start) > timeout:
            result = "ERROR: No Lidar data received for 10 seconds. Check that rosbridge and Gazebo are running."
            return result
        time.sleep(0.1) # Wait without busy-looping.

    pil_img = generate_lidar_radar_visual_pil(latest_scan_ranges)

    return [
        types.TextContent(type="text", text="[IMAGE SOURCE: 360-degree Lidar / Radar Map]"),
        _encode_image_to_imagecontent(pil_img)
    ]

@mcp.tool()
@logged_tool("LidarRadar")
def calculate_passage_width(target_angle_degrees: float) -> str:
    started = time.time()
    global latest_scan_ranges
    _ensure_ros_connection()

    if not ros_client.is_connected:                     # Require a live connection.
        result = "ERROR: No ROS connection; passage analysis unavailable."
        return result

    if latest_scan_ranges is None:
        result = "ERROR: Lidar data is unavailable."
        return result

    if not _is_lidar_fresh():                      # Reject stale data.
        age = time.time() - last_lidar_update
        result = f"WARNING: Lidar data is {age:.1f}s old and may be stale. Call get_lidar_radar_map for fresh data, then retry."
        return result

    num_beams = len(latest_scan_ranges)
    if num_beams == 0:
        result = "ERROR: Lidar data is empty."
        return result

    MIN_SAFE_WIDTH_METERS = 0.45
    MAX_SAFE_APPROACH_ANGLE = 35.0  # Degrees; reference threshold for oblique approaches.

    target_idx = int((target_angle_degrees % 360) / 360.0 * num_beams)
    window_deg = 30
    window_idx = int(window_deg / 360.0 * num_beams)

    min_left_distance = float('inf'); left_angle_deg = 0.0
    for i in range(window_idx):
        idx = (target_idx + i) % num_beams
        r = latest_scan_ranges[idx]
        if r is not None and 0.1 < r < 4.0 and r < min_left_distance:
            min_left_distance = r
            left_angle_deg = (idx / float(num_beams)) * 360.0

    min_right_distance = float('inf'); right_angle_deg = 0.0
    for i in range(window_idx):
        idx = (target_idx - i) % num_beams
        r = latest_scan_ranges[idx]
        if r is not None and 0.1 < r < 4.0 and r < min_right_distance:
            min_right_distance = r
            right_angle_deg = (idx / float(num_beams)) * 360.0

    if min_left_distance == float('inf') or min_right_distance == float('inf'):
        return f"No distinct edge or obstacle found at {target_angle_degrees:.1f} degrees. Area clear and SAFE."

    angle_difference_rad = math.radians(abs(left_angle_deg - right_angle_deg))
    width_squared = (min_left_distance**2) + (min_right_distance**2) - (2 * min_left_distance * min_right_distance * math.cos(angle_difference_rad))
    width_meters = math.sqrt(max(0, width_squared))

    # ============================================
    # APPROACH ANGLE CALCULATION
    # ============================================

    # Convert both edge points to robot-centered Cartesian coordinates.
    left_rad = math.radians(left_angle_deg)
    right_rad = math.radians(right_angle_deg)
    x1, y1 = min_left_distance * math.cos(left_rad), min_left_distance * math.sin(left_rad)
    x2, y2 = min_right_distance * math.cos(right_rad), min_right_distance * math.sin(right_rad)

    # Compute the passage line direction and its perpendicular normal.
    gate_dx, gate_dy = (x2 - x1), (y2 - y1)
    gate_line_angle = math.degrees(math.atan2(gate_dy, gate_dx))
    normal_angle = gate_line_angle + 90.0  # One of the two perpendicular approach directions.

    # Compare the target direction with the passage normal.
    difference = abs((target_angle_degrees - normal_angle + 180) % 360 - 180)
    approach_angle = min(difference, 180 - difference)  # 0 degrees is perpendicular; 90 degrees is parallel to the passage.

    effective_width = width_meters * math.cos(math.radians(approach_angle))

    status = "SAFE (Passable)" if effective_width >= MIN_SAFE_WIDTH_METERS else \
            ("RISKY (OBLIQUE APPROACH - Risk of getting stuck)" if width_meters >= MIN_SAFE_WIDTH_METERS else "RISKY (Too narrow - Risk of getting stuck)")

    report = (
        f"PASSAGE ANALYSIS ({target_angle_degrees} degrees):\n"
        f"- Left edge: {min_left_distance:.2f}m away ({left_angle_deg:.1f} degrees)\n"
        f"- Right edge: {min_right_distance:.2f}m away ({right_angle_deg:.1f} degrees)\n"
        f"- Raw passage width: {width_meters:.2f} meters\n"
        f"- Approach angle: {approach_angle:.1f} degrees (0 is perpendicular)\n"
        f"- Effective width (including approach angle): {effective_width:.2f} meters\n"
        f"- Robot minimum clearance: {MIN_SAFE_WIDTH_METERS} meters\n"
        f"DECISION: {status}"
    )
    if "OBLIQUE" in status:
        report += (
            f"\n[RECOMMENDATION]: Do not cross at this angle. Use a waypoint to align closer to the passage normal, approximately {normal_angle:.1f} degrees. "
            f"See the Oblique Approach Waypoint Rule in the protocol."
        )
        report += (
            "\n\n[SYSTEM REMINDER]: The passage is risky due to the oblique "
            "angle. Please review **Section 3: Stage 3 (Passage verification "
            "and Oblique Approach)** to determine your next exact maneuvers."
        )

    return report

if __name__ == "__main__":
    time.sleep(1) # Allow one second for the ROS connection to settle.
    mcp.run()
