# Ejecuta las pruebas unitarias y de aceptación dentro de la red de Compose.
# Contexto de build: raíz del repositorio.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /repo

COPY services/api/requirements.txt services/api/requirements.txt
COPY services/consumer/requirements.txt services/consumer/requirements.txt
COPY tests/acceptance/requirements.txt tests/acceptance/requirements.txt
RUN pip install --no-cache-dir \
    -r services/api/requirements.txt \
    -r services/consumer/requirements.txt \
    -r tests/acceptance/requirements.txt

COPY pytest.ini .
COPY contracts ./contracts
COPY services ./services
COPY scripts ./scripts
COPY tests ./tests

ENTRYPOINT ["pytest"]
CMD ["-v"]
