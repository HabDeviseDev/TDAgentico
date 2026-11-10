# Contexto de build: raíz del repositorio (reutiliza contracts/fleet_contracts).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY contracts/fleet_contracts ./fleet_contracts
COPY services/agent/fleet_agent ./fleet_agent

# Un solo proceso: un circuit breaker y un límite de concurrencia por instancia.
EXPOSE 8100
CMD ["uvicorn", "fleet_agent.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
