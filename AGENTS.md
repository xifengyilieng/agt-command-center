# AGT repository instructions

For install/run requests, read README.md and AGENT_INSTALL.md and perform the installation plus health checks. This is a local Windows application, not an Agent skill or a GitHub Pages dashboard.

- Default: browser mode via setup.ps1. Wallpaper Engine and login startup require those features to be part of the user's request.
- Python 3.10+ standard library is sufficient to run. Node/Playwright are development-only dependencies.
- Use the current user's data, including CODEX_HOME / DSH_HOME when provided. Never copy publisher data, tokens, personal paths, screenshots or logs.
- Do not fabricate tasks when no data source is found. Report service readiness and actual data availability separately.
- Bind to loopback. Keep .wallpaper-token, connection.js, generated pages, artifacts, backups, logs and local installation records out of version control.
- Preserve HALOFLUX source, attribution, Required Notice lines, noncommercial terms and the font license.
- After source changes run python build_screen.py and relevant Python tests; check runtime.js syntax after frontend changes. Use the isolated public browser test for installation-facing frontend changes.
- Do not overwrite unrelated Wallpaper Engine projects, change selected wallpapers or other monitors, or terminate unknown processes.
