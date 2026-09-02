# Code & Asset Cleanup Log

**Project:** Aether Surgical Console (REV 4.2)  
**Date:** August 2026  

---

## Cleaned Up Components & Assets

| File Removed | Classification | Reason | Evidence & References Checked |
|---|---|---|---|
| `Robot-Console/yolov8x.pt` | **DEFINITELY DEAD / DUPLICATE** | Exact 136.8 MB duplicate of root `yolov8x.pt`. Unused by Robot Console. | Full codebase search confirmed 0 references in `Robot-Console/` Python code, configuration, or startup scripts. Root `yolov8x.pt` remains active. |
| `screens/end_effector.py` | **DEFINITELY DEAD** | Standalone unreferenced screen module (`EndEffectorScreen`). | Full AST search confirmed 0 imports across the codebase; never registered in `main.py` or navigation bar. |
| `screens/postop_analytics.py` | **DEFINITELY DEAD** | Standalone unreferenced screen module (`PostopAnalyticsScreen`). | Full AST search confirmed 0 imports across the codebase; never registered in `main.py` or navigation bar. |
| `screens/telemetry.py` | **DEFINITELY DEAD** | Standalone unreferenced screen module (`TelemetryScreen`). | Full AST search confirmed 0 imports across the codebase; superseded by `screens/live_control.py`. |

---

## Retained Components
- `screens/preop_planning.py`: ACTIVE
- `screens/live_video.py`: ACTIVE
- `screens/live_control.py`: ACTIVE
- `screens/settings.py`: ACTIVE
- `screens/comm_center.py`: ACTIVE
- `yolov8x.pt`: ACTIVE in project root
