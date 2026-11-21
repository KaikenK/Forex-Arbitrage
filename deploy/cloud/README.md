# Arbex recorder in the cloud

This folder runs the live recorder (backend, :8000) and the news reader (semantic service, :9000) on a small Linux server, so recording does not depend on the laptop being on. The dashboard and demo stay on the laptop.

## Server (Ubuntu 24.04, 2 vCPU, 4 GB, about 30 GB disk)

Copy this folder to the server, then run:

```
sudo bash setup.sh
```

The script does the following:
- installs git and Python 3.14 (through uv);
- clones the `semantic-microservice` branch to `/opt/arbex/app` (set `COMMIT=<sha>` to pin an exact commit for a research sample);
- installs the pinned packages (`requirements-cloud.txt`) and CPU-only torch;
- caches FinBERT;
- installs two systemd services, `arbex-semantic` and `arbex-backend`, which run as the user `arbex` and restart after a failure or a reboot.

Configuration matches the laptop's live launcher:
- FinBERT, the release model and the currency bias are on;
- the Phase 2 switches are on (the risk penalty is applied, direction never is);
- the C43 shadow is on.

All output goes under `/var/lib/arbex`:

| Folder | Contents |
|---|---|
| `recordings/<UTC day>/` | `spot.jsonl`, `opportunities.jsonl` |
| `shadow/<UTC day>/` | `semantic_shadow.jsonl` |
| `runtime/predictions/` | `live_predictions.jsonl` |
| `cache/calendar/archive/` | weekly economic calendar |

**API keys (optional).** Put the laptop's `services/semantic/config.toml` at `/etc/arbex/config.toml`. Without it the RSS feeds still work and only Finnhub is off. The key file is copied by the owner, never committed.

**Health check:** run `arbex-status` on the server.

**Logs:** `journalctl -u arbex-backend -u arbex-semantic`.

**Ports:** only SSH (22) should be open. The services listen on the server and are viewed through an SSH tunnel.

## Laptop (`laptop/`, installed in `C:\Users\xyreo\arbex\cloud`)

The scripts use the key `C:\Users\xyreo\.ssh\arbex_azure` and the address in `host.txt` (`azureuser@<server IP>`).

| Script | What it does |
|---|---|
| `cloud_status.bat` | Runs `arbex-status` on the server. |
| `cloud_pull.bat` | Copies new recordings, shadow rows, the prediction log and the calendar archive to `C:\Users\xyreo\arbex\cloud_data`. Finished days are copied once; the last two days are copied again. |
| `cloud_tunnel.bat` | Private view of the cloud live monitor at http://localhost:18000/live. |

## Research rule

The cloud recording is a new sample (RESEARCH_TRACKER §6, C44):
- It is never pooled with the laptop sample (25 Oct – 7 Nov 2026).
- No outcome is examined before 30 weekdays are recorded and its analysis plan is logged.
- Counts, gaps and per-venue quality can be checked at any time.
