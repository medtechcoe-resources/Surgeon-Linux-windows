# Fix Baseline Document

**Project:** Aether Surgical Console (REV 4.2)  
**Date:** August 2026  
**Status:** Baseline Established Before Fix Implementation

---

## 1. Startup Information
- **Main Application Entry Point:** `main.py` (Surgeon Console)
  ```powershell
  python main.py
  ```
- **System Launcher:** `launch_all.py` / `launchall.py` (Orchestrates Broker, Data Generator, Surgeon Console, Robot Console, Observer Screen)
  ```powershell
  python launch_all.py
  ```

---

## 2. Test Suite Baseline
- **Command:** `pytest -v`
- **Total Collected Tests:** 35
- **Passing Tests:** 35 (100%)
- **Failing Tests:** 0
- **Execution Time:** ~12.63s

### Test Modules Tested:
1. `tests/test_auth_database.py` (4 tests - database, sessions, ACLs, device verification)
2. `tests/test_broker_security.py` (3 tests - send_bytes sync, disconnects, logout)
3. `tests/test_connection_lifecycle.py` (3 tests - disconnect self-join, backoff, auth clearing)
4. `tests/test_protocol.py` (19 tests - message classification, size limits, envelopes, roundtrips)
5. `tests/test_video_auth.py` (3 tests - mTLS video connection authorization & rejection)
6. `tests/test_video_stream.py` (1 test - TCP video streaming roundtrip)
7. `tests/test_yolo_pipeline.py` (2 tests - pipeline stop methods)

---

## 3. Known Existing Warnings / Deficiencies (from Audit)
1. Synchronous YOLO inference blocking Qt GUI thread in `yolo_pipeline.py`.
2. Missing message dispatch in `main.py:_on_message_received` (pass stub).
3. Unconnected telemetry displays in `screens/live_control.py`.
4. Silent exception handling `except: pass` in `screens/live_control.py`.
5. Duplicate unreferenced file `Robot-Console/yolov8x.pt` (136.8 MB).
6. Unused dead screen files in `screens/` (`end_effector.py`, `postop_analytics.py`, `telemetry.py`).
7. Stale Fernet text in `screens/settings.py`.
