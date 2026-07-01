# ─────────────────────────────────────────────────────────────────────────────
# AxBi frontend image — multi-stage: build the Vite bundle, serve via nginx.
# nginx also reverse-proxies /api and /ws to the backend so the SPA stays
# same-origin (no CORS config needed; frontend already calls relative /api).
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: build ──
FROM node:20-alpine AS build
WORKDIR /app

# Supabase keys are baked at BUILD time (Vite inlines import.meta.env.*).
# Passed as build args from docker-compose (which reads them from .env).
# The anon key is public-safe by design.
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ENV VITE_SUPABASE_URL=$VITE_SUPABASE_URL \
    VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# ── Stage 2: serve ──
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
