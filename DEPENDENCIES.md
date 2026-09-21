# COMSOL MCP Dependencies & Runtime Environment Lock

## 1. Platform Matrix & Verification Status

| Platform | COMSOL Version | Architecture | JDK Runtime | Verification Status | Notes |
|---|---|---|---|---|---|
| macOS (Darwin 27.0) | COMSOL 6.4 (Build 6.4.0.293) | arm64 (Apple Silicon) | Amazon Corretto 11.0.31 | **VERIFIED** | Active verification environment |
| macOS | COMSOL 6.4 | x86_64 (Intel) | JDK 11+ | UNVERIFIED | Platform adapters maintain compatibility |
| macOS | COMSOL 6.3 | arm64 / x86_64 | JDK 11+ | UNVERIFIED | Version boundaries preserved |
| Windows | COMSOL 6.4 | x86_64 | JDK 11+ | UNVERIFIED | ACL, process & classpath boundaries preserved |
| Windows | COMSOL 6.3 | x86_64 | JDK 11+ | UNVERIFIED | Preserved in platform abstraction |

---

## 2. Pinned Python Environment (macOS arm64)

- **Python Version**: CPython 3.13.14 (darwin / arm64)
- **Virtual Environment Tool**: uv / venv
- **Test Runner**: pytest 9.1.1, pluggy 1.6.0, anyio 4.15.1

### Exact Package Lock

```text
anyio==4.15.1
annotated-types==0.8.0
attrs==26.1.0
certifi==2026.7.22
cffi==2.1.1
click==8.5.0
comsol-mcp==0.1.9
cryptography==50.0.1
h11==0.16.0
httpcore==1.0.9
httpcore2==2.13.0
httpx==0.28.1
httpx-sse==0.4.3
httpx2==2.13.0
idna==3.20
iniconfig==2.3.0
jpype1==1.7.1
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
mcp==1.30.0
mcp-types==2.2.0
MPh==1.4.0
numpy==2.5.3
opentelemetry-api==1.44.0
packaging==26.3
pluggy==1.6.0
pycparser==3.0
pydantic==2.13.5
pydantic_core==2.46.5
pydantic-settings==2.15.0
Pygments==2.21.0
PyJWT==2.14.0
pytest==9.1.1
python-dotenv==1.2.3
python-multipart==0.0.32
referencing==0.37.0
rpds-py==2026.6.3
sse-starlette==3.4.11
starlette==1.6.0
truststore==0.10.4
typing-inspection==0.4.4
typing_extensions==4.16.0
uvicorn==0.53.0
```

---

## 3. Java & COMSOL Runtime Boundary

- **COMSOL Installation Path**: `/Applications/COMSOL64/Multiphysics`
- **JDK Home**: `/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home`
- **Worker Classpath Requirements**:
  - `com.comsol.model.util.ModelUtil`
  - `com.comsol.model.*` (API plugins: `plugins/com.comsol.api.*.jar`, `apiplugins/com.comsol.api.*.jar`)
- **Shared Server Boundary**:
  - Existing running server instances (e.g. PID 5014 on port 56389) are shared host resources.
  - Automated tests and client sessions must never terminate (kill) the shared server.
  - Task-owned worker processes maintain independent lifecycle tracking and unique generation numbers.
