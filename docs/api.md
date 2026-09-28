# API Specification

## Base URL
- Development: `http://localhost:8000/api/v1`

## Key Endpoints

### 1. Chat & Sessions
- `POST /chat/message`
  - **Body**: `{ "session_id": "str", "message": "str", "attachments": [] }`
  - **Response**: `{ "response": "str", "sentiment": "str", "escalated": bool }`
- `GET /chat/sessions/{session_id}/history`
  - **Response**: List of chronological chat events.

### 2. Knowledge & RAG
- `POST /knowledge/query`
  - **Body**: `{ "query": "str", "top_k": 3 }`
  - **Response**: Grounded context snippets and source references.
- `POST /knowledge/upload`
  - **Body**: Multipart form data with document file for ingestion.

### 3. Tickets & Escalation
- `POST /tickets`
  - **Body**: `{ "title": "str", "description": "str", "priority": "high", "customer_id": "str" }`
  - **Response**: Ticket details with SLA metadata.
- `GET /tickets/{ticket_id}`
  - **Response**: Ticket status, assigned agent, and SLA timers.
- `POST /tickets/{ticket_id}/escalate`
  - **Body**: `{ "reason": "str", "target_tier": "tier_2" }`
  - **Response**: Escalation status and routing confirmation.
