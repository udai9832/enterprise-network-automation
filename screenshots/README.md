# Screenshots (Optional)

Optional checklist for capturing screenshots from a live deployment of the reference lab ([docs/GNS3_LAB_SETUP.md](../docs/GNS3_LAB_SETUP.md)).

No live GNS3 deployment was performed as part of this project, so this folder contains no screenshots. Simulated, mock-backed demonstration output is available as text in [docs/demo_output/](../docs/demo_output/). Screenshots should only ever be added here if they come from a real lab run.

## Checklist

| File | Shows |
|---|---|
| `gns3_topology.png` | GNS3 canvas with R1, R2, SW1 (and the Cloud node), readable labels |
| `connectivity_check.png` | Terminal: `python -m src.network_check` table and summary |
| `device_info_collection.png` | Terminal: `python -m src.device_info` table and summary |
| `config_backup.png` | Terminal: `python -m src.config_backup` table and summary (not the configuration contents) |
| `config_validation.png` | Terminal: `python -m src.config_validation` PASS/FAIL table and summary |
| `pytest_results.png` (optional) | Terminal: `python -m pytest -q` final result |

## Privacy Rules

Before saving a screenshot, check that it does **not** show:

- the lab password, `NETWORK_PASSWORD`, or the commands used to set it
- configuration contents, password hashes or secrets from `data/backups/`
- your Windows username or personal folders (crop the terminal title bar and prompt if needed)
- browser tabs, notifications, email, chat or other personal windows
- GitHub accounts other than the project repository
- your real home/office IP addresses or network adapter details

Screenshots should show only GNS3, the project terminal output and safe project paths.

Tips: use a clean terminal window, run `cls` before each command, and crop to the relevant output.
