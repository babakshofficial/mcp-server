FROM node:20-alpine AS dashboard-build

WORKDIR /app/dashboard
COPY dashboard/package.json ./
RUN npm install
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SYNC_MCP_HOST=0.0.0.0 \
    SYNC_MCP_PORT=8080 \
    SYNC_MCP_DATA_DIR=/app/data \
    SYNC_MCP_DASHBOARD_DIST=/app/dashboard/dist

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY sync_mcp ./sync_mcp
COPY --from=dashboard-build /app/dashboard/dist ./dashboard/dist

EXPOSE 8080
CMD ["python", "-m", "sync_mcp"]
