from experiment_log import logged_tool
from fastmcp import FastMCP
import datetime
import os
import time
import json

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

# Create the cartographer MCP server.
mcp = FastMCP("Cartographer")

# Store the map alongside LTM and STM in the runtime memory directory.
# Resolve from __file__ so the working directory does not affect the path.
MEMORY_DIR = os.path.normpath(os.path.join(MODULE_DIR, "..", "memory"))
os.makedirs(MEMORY_DIR, exist_ok=True)
MAP_FILE_PATH = os.path.join(MEMORY_DIR, "cognitive_map.md")


@mcp.tool()
@logged_tool("Cartographer")
def update_map(mermaid_code: str) -> str:
    """Overwrite the complete Mermaid topological map after an LTM update.

    Include all existing nodes and edges as well as new discoveries.
    mermaid_code must contain valid Mermaid source without Markdown fences.
    Example: graph TD; A[Start] -->|Forward| B[Door]"""
    started = time.time()
    try:
        updated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Create or overwrite the Markdown file.
        with open(MAP_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(f"# CARS Cognitive Topological Map\n\n")
            f.write(f"*Last updated: {updated_at}*\n\n")
            f.write("```mermaid\n")
            f.write(mermaid_code.strip() + "\n")
            f.write("\n```\n")
        result = f"Map updated successfully. File: {MAP_FILE_PATH}"
    except Exception as e:
        result = f"ERROR: Failed to save the map: {str(e)}"

    return result

if __name__ == "__main__":
    mcp.run()
