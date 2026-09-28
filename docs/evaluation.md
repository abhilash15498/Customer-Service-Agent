# Evaluation & Quality Assurance

## 1. RAG Grounding & Hallucination Assessment
- **Faithfulness**: Verifies whether the generated answer relies solely on retrieved knowledge base context.
- **Context Relevance**: Evaluates whether retrieved chunks accurately match customer questions.
- **Answer Relevance**: Measures whether customer intents were resolved without digression.

## 2. Sentiment & Escalation Precision
- **Confusion Matrix on Frustration Detection**: False positives vs. missed escalations.
- **Escalation Accuracy**: Ensures high-urgency or contract-risk customers reach human reps in < 60s.

## 3. SLA Compliance Metrics
- **First Response Time (FRT)**: Automated response latency (< 2.5s).
- **Resolution Rate (FCR)**: Percentage of issues resolved without human agent intervention.
- **Escalation SLA Breaches**: Tracks whether escalated tickets receive human acknowledgement within defined thresholds.
