# Aether Surgical Console — Offline Local-Network Medical Workstation

A production-hardened, **100% offline, local-network medical robotic workstation system** built with Python and PyQt6.

---

## 🔒 Offline & Zero-Trust Architecture

- **100% Offline**: Zero external network requests, no cloud SaaS, no hosted broker, no telemetry beacons.
- **TLS 1.3 & mTLS**: All TCP transport is secured with local certificates issued by an offline Local Certificate Authority (`Aether Local CA`).
- **Role-Based Access Control (RBAC)**: Topic-level access control backed by local SQLite database with bcrypt password hashing and session tokens.
- **Dedicated Video Channel (Port 5001)**: High-performance raw length-prefixed JPEG binary streaming strictly 1-to-1 between **Robot Console** (only video source) and **Surgeon Console** (only video receiver). Observer screens and Data Generator are strictly prohibited from receiving or transmitting video.
- **Resilient Networking**: Decoupled TCP connection lifecycle from authentication session state, automatic exponential backoff reconnection (`0.5s` to `10.0s`), and fine-grained per-client send locking.

---

## 📂 Project Structure

```text
Surgeon Console UI/
├── main.py                     # Surgeon Console UI application
├── shared_networking/          # Shared offline networking & security library
│   ├── broker.py               # Local Pub/Sub TCP/TLS broker with fine-grained locks & RBAC
│   ├── connection_manager.py   # Client TCP/TLS connection manager with exponential backoff
│   ├── database.py             # SQLite DB: user auth, session tokens, audit logging, ACLs
│   ├── tls.py                  # TLS 1.3 / mTLS context manager and certificate utilities
│   ├── video_stream.py         # Dedicated Port 5001 authenticated VideoReceiver
│   ├── protocol.py             # Binary framing, message envelope, serialization, limits
│   └── config.py               # Local network host, ports, intervals, payload bounds
├── Robot-Console/              # Robot Operating Station UI & services
│   ├── main.py                 # Robot Console entry point
│   ├── services/video_broadcaster.py # Authenticated VideoBroadcastService
│   └── ui/                     # Multi-tab robot telemetry and control interface
├── Observer-Screen/            # Observer display (strictly telemetry/vitals, NO video)
├── Data-Generator/             # Local test data publisher for vitals and telemetry
├── screens/                    # Surgeon Console UI screens (Planning, Video, Control, Analytics)
├── tests/                      # Comprehensive test suite (35+ unit and regression tests)
├── requirements.txt            # Pinned dependencies for offline workstation installation
└── pyproject.toml              # Build system configuration
```

---

## ⚡ Quick Start

### 1. Installation
Install dependencies in your offline Python 3.10+ virtual environment:
```bash
pip install -r requirements.txt
```

### 2. First-Time Provisioning
Generate the offline root CA, device certificates, and initialize the local SQLite database:
```bash
python -m shared_networking.provisioning
```

### 3. Run Applications
- **Surgeon Console UI**:
  ```bash
  python main.py
  ```
- **Robot Console UI**:
  ```bash
  python Robot-Console/main.py
  ```
- **Observer Screen**:
  ```bash
  python Observer-Screen/main.py
  ```
- **Data Generator (Simulation)**:
  ```bash
  python Data-Generator/main.py
  ```

---

## 🧪 Testing

Run the automated test suite verifying protocol framing, authentication, broker locking, connection lifecycle, and video channel authorization:
```bash
pytest -v
```

---

## 👁️ Computer Vision & YOLO Notes

The live video pipeline integrates local offline YOLO inference via `ultralytics`.
- The bundled `yolov8x.pt` is the standard COCO 80-class pre-trained model (classes: `person`, `cup`, `scissors`, etc.).
- For surgical deployment, swap `yolov8x.pt` with custom-trained weights (e.g. EndoVis / Cholec80 surgical instrument datasets) by placing the trained `.pt` file in the project root or updating the model path in `screens/live_video.py`.
