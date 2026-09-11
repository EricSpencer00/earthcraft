# Local progress dashboard

Run `.venv/bin/python scripts/dashboard.py` from the repository, then open http://127.0.0.1:8765. It uses the existing Python environment and no remote UI assets or model calls. The server binds only to loopback and exposes read-only routes. Close the server process to stop it; stopping the dashboard does not stop generation.

The UI switches between the Chicago city draft and downtown test, toggles automatic observation updates, refreshes manually and shows the generator's log. The map overlays the municipal boundary on region-file coordinates. Its green cells represent allocated chunk entries with sectors present on disk, not verified complete terrain or buildings. Template chunks can be counted. Reading a region while it is changing is an observation, not a consistency or playability check. Source recovery, stale status, missing drive, and disconnected feed have explicit states. Memory is the latest supervisor observation, not a fresh dashboard measurement.

Each region is 512 blocks across. The city uses the existing approximate generator coordinate frame. The downtown view is a coverage grid without a city-boundary overlay. Download batches and the bounded recent log are not a total download percentage or ETA.

The dashboard does not pause, kill, restart, or modify the generator. Its controls affect observation only. World rendering, queue scheduling, tile resume, and multi-project registration remain future implementation work.
