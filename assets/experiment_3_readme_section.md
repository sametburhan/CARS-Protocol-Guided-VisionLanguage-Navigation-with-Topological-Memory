## Experiment 3: Passage Navigation & Multi-Door Choice (CARS Protocol)

In this scenario, the robot is placed in a separate room from the target. It must autonomously survey the environment, evaluate multiple doorway candidates, and select a safe passage to reach the target in the adjacent room.

### Preparatory 360° Exploration Spin
In Experiment 3, Pose 2, all four model/protocol conditions received a separately requested six-stop survey before the target command. This CARS preview illustrates that preparation, not a guarantee of comprehensive mapping.

<p align="center">
  <img src="360_spin_timelapse.gif" alt="360 Degree Exploration Spin" width="60%">
  <br>
  <em>Timelapse of the initial environment survey.</em>
</p>

### Autonomous Route Selection & Target Approach
The two CARS runs selected different routes and eligible bins: GPT chose the left passage and Gemini the right passage in the overhead view. Final reported target ranges were 0.31 m and 0.37 m respectively, within the accepted 0.30 m +/- 0.15 m tolerance.

| GPT (Selected Left Passage) | Gemini (Selected Right Passage) |
| :---: | :---: |
| <img src="gpt_left_run.gif" alt="GPT Left Run" width="100%"> | <img src="gemini_right_run.gif" alt="Gemini Right Run" width="100%"> |
| *GPT navigates through Wood Framed Passage C.* | *Gemini navigates through Passage 1.* |

### Generated Cognitive Topological Maps
These are agent-authored Mermaid maps saved by Cartographer, not automatically generated from LTM. GPT labels below use display-friendly spacing; original outputs are preserved in [GPT map](cognitive_map_gpt_cars.md) and [Gemini map](cognitive_map_gemini_cars.md).

#### GPT Map
```mermaid
graph TD
    Starting_Point[Starting Point] -->|Through Wood Framed Passage C| Room_2[Room 2]
    Starting_Point -->|Ahead at spin stop 1| Wide_Framed_Passage_A((Wide Framed Passage A))
    Starting_Point -->|Ahead at spin stop 2| Brick_Framed_Passage_B((Brick Framed Passage B))
    Starting_Point -->|Along wall, left/front at spin stop 4| Wooden_Shelving_Unit{Wooden Shelving Unit}
    Starting_Point -->|Left/front at spin stop 5| Wood_Framed_Passage_C((Wood Framed Passage C))
    Room_2 -->|Against brick wall| green_trash_bin((Green Trash Bin))
```

#### Gemini Map
```mermaid
graph TD
    Starting_Point[Starting_Point] -->|Through_Passage_1| Room_2[Room_2]
    Starting_Point -->|Ahead_Through_Brick_Wall| Passage_1((Passage_1))
    Starting_Point -->|Left_Wall_Mounted| Wooden_Bookshelf{Wooden_Bookshelf}
    Starting_Point -->|Right_Side_Through_Brick_Wall| Passage_2((Passage_2))
    Room_2 -->|Beside_Wooden_Divider| Green_Trash_Bin((Green_Trash_Bin))

```
