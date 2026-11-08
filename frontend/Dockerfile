# syntax=docker/dockerfile:1
FROM node:24.22.2-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS build
WORKDIR /build
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts playwright.config.ts index.html ./
COPY src/ ./src/
COPY public/ ./public/
COPY tests/ ./tests/
RUN npm run build

FROM nginx/nginx-unprivileged:1.31.8-alpine-slim@sha256:633f37ad70b9fd48d4da573db1ebc9111a9ad174e5178e83bd2ef45381fde5e2 AS runtime
ENV PORT=8080 \
    API_UPSTREAM=http://api:8080 \
    NGINX_RESOLVER=127.0.0.11 \
    NGINX_ENVSUBST_FILTER="^(PORT|API_UPSTREAM|NGINX_RESOLVER)$"
COPY --from=build /build/dist/ /usr/share/nginx/html/
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
USER 101:101
EXPOSE 8080
