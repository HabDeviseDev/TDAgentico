# Ejecuta las pruebas de aceptación dentro de la red de Docker Compose.
# Contexto de build: raíz del repositorio.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /repo

COPY tests/acceptance/requirements.txt tests/acceptance/requirements.txt
RUN pip install --no-cache-dir -r tests/acceptance/requirements.txt

COPY pytest.ini .
COPY contracts ./contracts
COPY tests ./tests

ENTRYPOINT ["pytest"]
CMD ["-v"]
