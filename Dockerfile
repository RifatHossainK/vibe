# Vibe — single-container image: Next.js static export + FastAPI backend.
# The backend serves the frontend files + /api + WebSocket on one port ($PORT),
# so real-time messaging works on a single Render free service (no WS proxy needed).
#
# Stage 1: build the frontend static export.
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# IMPORTANT: never bake the dev WS URL into the production bundle.
# .env.local sets NEXT_PUBLIC_WS_URL=ws://localhost:8000 for local dev only.
# Removing it here makes the browser use same-origin ws(s)://<host>/api/ws.
RUN rm -f .env.local
RUN npm run build

# Stage 2: Python backend + the built frontend.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/out ./frontend/out
WORKDIR /app/backend
ENV PORT=8000
EXPOSE 8000
CMD ["python", "run.py"]
