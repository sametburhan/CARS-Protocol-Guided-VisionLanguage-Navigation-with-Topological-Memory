#!/bin/bash

echo "Starting ROS 2 systems..."

# Load the ROS 2 environment.
source /opt/ros/humble/setup.bash

# Waffle includes a camera; select the model before launching Gazebo.
export TURTLEBOT3_MODEL=waffle

# Stop background ROS processes when Ctrl+C is pressed.
trap "echo -e '\nShutting down the system; stopping all nodes.'; kill 0" SIGINT

# Start the Gazebo simulation.
echo "Starting the TurtleBot3 House simulation..."
ros2 launch turtlebot3_gazebo turtlebot3_house.launch.py &

# Allow time for Gazebo to start; increase this delay if necessary.
sleep 8

# Start rosbridge.
echo "Starting the rosbridge WebSocket server..."
ros2 launch rosbridge_server rosbridge_websocket_launch.xml &

echo "All systems launched; waiting for the MCP client."
echo "Press Ctrl+C to stop the system."

# Keep the launcher alive while background processes run.
wait
