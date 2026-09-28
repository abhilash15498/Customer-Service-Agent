# Customer Service AI

An enterprise-ready customer support platform featuring RAG, sentiment-driven escalation, ticket routing, SLA monitoring, and multimodal document processing.

## 📁 Project Architecture

```
customer-service-ai/
│
├── backend/
│   ├── app/
│   │   ├── api/             # API routes and controllers
│   │   ├── core/            # Configuration, security, database sessions
│   │   ├── models/          # SQLAlchemy ORM models
│   │   ├── schemas/         # Pydantic request/response schemas
│   │   │
│   │   ├── services/        # Business logic & AI pipelines
│   │   │   ├── sentiment/   # Real-time sentiment & intent analysis
│   │   │   ├── escalation/  # Human handover & trigger thresholds
│   │   │   ├── tickets/     # Ticket lifecycle management
│   │   │   ├── sla/         # SLA tracking & breach monitoring
│   │   │   ├── routing/     # Intelligent department & agent assignment
│   │   │   ├── rag/         # Vector search & grounded answer generation
│   │   │   ├── knowledge/   # KB indexing, chunking, and ingestion
│   │   │   ├── multimodal/  # Image, receipt, and audio processing
│   │   │   └── sessions/    # Chat session state & conversation memory
│   │   │
│   │   ├── workers/         # Celery background jobs & event tasks
│   │   └── main.py          # FastAPI application entry point
│   │
│   ├── tests/               # Unit and integration tests
│   └── requirements.txt     # Python backend dependencies
│
├── frontend/                # Customer chat widget & agent dashboard
├── knowledge_base/          # Source documents and local vector store
├── uploads/                 # Temporary and persistent file uploads
├── docker/                  # Dockerfiles and container configurations
├── docs/                    # Architecture, API specifications, and evaluation
│   ├── architecture.md
│   ├── api.md
│   └── evaluation.md
│
├── .env.example             # Environment variable template
├── docker-compose.yml       # Multi-service container orchestration
└── README.md                # Project documentation
```

## 🚀 Quick Start

### 1. Environment Setup
```bash
cp .env.example .env
```

### 2. Run with Docker Compose
```bash
docker-compose up --build
```

### 3. Local Backend Development
```bash
cd backend
python -m venv venv
venv\Scripts\activate   # On Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
API Documentation will be available at `http://localhost:8000/docs`.
