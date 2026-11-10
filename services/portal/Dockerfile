# Contexto de build: services/portal
FROM node:20.18-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# Pruebas del frontend: docker build --target test services/portal
FROM build AS test
RUN npm test

FROM nginx:1.27-alpine
ENV API_UPSTREAM=api:8000 \
    AGENT_UPSTREAM=agent:8100 \
    PORTAL_CHAT_READ_TIMEOUT=90s \
    PORTAL_STALE_AFTER_SECONDS=120 \
    PORTAL_CONNECTION_TIMEOUT_SECONDS=45 \
    PORTAL_SSE_READ_TIMEOUT=120s
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
