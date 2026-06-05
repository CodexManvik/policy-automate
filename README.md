# ReAssure 3.0 Claims Auto-Adjudication Engine

An automated health insurance claims auto-adjudication system implementing the ReAssure 3.0 product rules with a FastAPI backend and a Vite React frontend.

## Architecture
The system utilizes a modern decoupled web architecture:
1. **Core Adjudication Pipeline:** A 7-gate sequential Python pipeline that executes deterministic calculations for waiting periods, room pro-rata, copayments, deductibles, and sum-insured waterfalls.
2. **AI Inference & Semantic Agent:** An LLM-backed reasoning agent that handles unstructured claim evaluation, exclusion reasoning, and GBNF grammar-enforced JSON generation.
3. **FastAPI Microservice API:** An async FastAPI layer exposing endpoints for real-time claim context processing and auto-routing.
4. **Vite React Frontend:** A glassmorphic web dashboard providing claim assembly forms, preset test scenarios, visual sum-insured waterfalls, and step-by-step audit logs.

## Prerequisites
- Python 3.11+
- Node.js 20+
- npm or uv package manager

## Environment Setup
Create a `.env` file in the root directory:
```bash
LLM_PROVIDER=mock
LLM_URL=http://localhost:8080
```

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Install Frontend dependencies:
```bash
cd frontend
npm install
```

## Run

1. Start the FastAPI API Server:
```bash
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
```

2. Start the Frontend Development Server:
```bash
cd frontend
npm run dev
```

## API Documentation
The API endpoints are documented interactively:
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

Primary Endpoints:
- `POST /api/v2/adjudicate`: Accepts a full ClaimContext payload and returns the final ClaimDecision with deduction breakdowns and rule evaluation audit traces.
