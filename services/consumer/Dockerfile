# Contexto de build: raíz del repositorio (ver docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY services/consumer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY contracts/fleet_contracts ./fleet_contracts
COPY services/consumer/consumer ./consumer

CMD ["python", "-m", "consumer.main"]
