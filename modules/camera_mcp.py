from experiment_log import logged_tool
from fastmcp import FastMCP
import mcp.types as types
import roslibpy
import time
import base64
import numpy as np
from PIL import Image as PILImage
import os
import io
import time
import json

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
mcp = FastMCP("Camera")
CAMERA_VISUAL_REMINDER = """
[VISUAL CHECK BEFORE FORWARD MOTION]
Identify your immediate waypoint in this image: the selected passage,
a repositioning point, or the task object. State its position
(left / approximately center / right) and what lies directly ahead.

For passage entry, confirm that this is the intended passage and that
the forward travel corridor enters its opening rather than its frame
or an adjacent wall. If not aligned, turn or reposition safely, then
inspect a fresh image before entry.

A completed turn or SAFE width result does not prove visual alignment.
Distant objects do not require pixel-perfect centering.
Confirm clearance separately with radar and numeric measurements.
""".strip()

# ==========================================
# GLOBAL ROS CONNECTION AND TOPICS
# ==========================================
ros_client = roslibpy.Ros(host='127.0.0.1', port=9090)
# Create the shared topic object once rather than for every call.
camera_topic = roslibpy.Topic(ros_client, '/camera/image_raw', 'sensor_msgs/msg/Image', queue_length=1)


def ensure_connection():
    """Reconnect automatically if the ROS connection is lost."""
    if not ros_client.is_connected:
        print("ROS disconnected. Reconnecting...")
        try:
            ros_client.run()
            time.sleep(1.0)
        except Exception as e:
            print(f"Reconnection failed: {e}")

# Attempt the initial connection when the server starts.
ensure_connection()
if ros_client.is_connected:
    print("Camera MCP server: Connected to ROS bridge. Automatic reconnect and fresh-frame capture enabled.")
    
# ==========================================
# ON-DEMAND CAMERA TOOLS
# ==========================================
@mcp.tool()
@logged_tool("Camera")
def get_detailed_camera():
    """Capture and process a detailed raw frame for room or object inspection.

    Use for a new room, an ambiguous view, or an object search. Frames are
    resized to at most 1920x1080 and JPEG quality 85. Returns a source label
    and the encoded JPEG as MCP image content."""
    started = time.time()
    ensure_connection()

    if not ros_client.is_connected:           # Require a live connection.
        result = "ERROR: No ROS connection; camera image unavailable."
        return result

    snapshot = {"msg": None}
    
    def snapshot_callback(msg):
        if snapshot["msg"] is None:
            snapshot["msg"] = msg

    topic = roslibpy.Topic(
        ros_client, 
        '/camera/image_raw', 
        'sensor_msgs/msg/Image', 
        queue_length=1
    )
    topic.subscribe(snapshot_callback)
    
    timeout = 15.0
    start = time.time()
    
    while snapshot["msg"] is None:
        if (time.time() - start) > timeout:
            topic.unsubscribe()
            result = "ERROR: No data received from the detailed raw camera."
            return result
        time.sleep(0.05)

    topic.unsubscribe()
    
    try:
        msg = snapshot["msg"]
        img_bytes = base64.b64decode(msg['data'])
        img_np = np.frombuffer(img_bytes, dtype=np.uint8)
        
        width = msg['width']
        height = msg['height']
        img_np = img_np.reshape((height, width, 3))
        
        img_pil = PILImage.fromarray(img_np, 'RGB')
        
        # Bound the image size to reduce payload size.
        # Limit resolution to 1920x1080 and JPEG quality to 85.
        img_pil.thumbnail((1920, 1080))
        buffer = io.BytesIO()
        img_pil.save(buffer, format="JPEG", quality=85, optimize=True)
        
        return [
            types.TextContent(
                type="text",
                text="[IMAGE SOURCE: Detailed Analysis Camera]\n\n"
                     + CAMERA_VISUAL_REMINDER,
            ),
            types.ImageContent(
                type="image",
                data=base64.b64encode(buffer.getvalue()).decode("ascii"),
                mimeType="image/jpeg",
            ),
        ]

    except Exception as e:
        result = f"ERROR: Raw image processing failed: {str(e)}"
        return result


@mcp.tool()
@logged_tool("Camera")
def get_fast_camera():
    """Get a fresh compressed camera frame for navigation and passage detection.

    Returns a compact image with a source label."""
    started = time.time()
    ensure_connection()                 # Reconnect if needed.
    if not ros_client.is_connected:           # Require a live connection.
        result = "ERROR: No ROS connection; camera image unavailable."
        return result

    snapshot = {"data": None}
    
    def snapshot_callback(msg):
        if 'data' in msg and snapshot["data"] is None:
            snapshot["data"] = msg['data']

    # Subscribe only when the tool is called.
    topic = roslibpy.Topic(
        ros_client, 
        '/camera/image_raw/compressed', 
        'sensor_msgs/msg/CompressedImage', 
        queue_length=1 # Unsubscribe after the first frame to prevent stale buffering.
    )
    topic.subscribe(snapshot_callback)
    
    timeout = 10.0
    start = time.time()
    
    # Wait for the first frame.
    while snapshot["data"] is None:
        if (time.time() - start) > timeout:
            topic.unsubscribe()
            result = "ERROR: No data received from the fast camera."
            return result
        time.sleep(0.05)
        
    # Unsubscribe immediately after receiving the frame.
    # This prevents old frames from accumulating in the background.
    topic.unsubscribe()

    return [
        types.TextContent(
            type="text",
            text="[IMAGE SOURCE: Fast RGB Camera]\n\n"
                 + CAMERA_VISUAL_REMINDER,
        ),
        types.ImageContent(type="image", data=snapshot["data"], mimeType="image/jpeg")
    ]
    
if __name__ == "__main__":
    mcp.run()
