# ===========================================================================
# Stage 1: Build the React / Vite SPA
# ===========================================================================
FROM node:22-alpine AS build

WORKDIR /app

# Enable pnpm (version 9 matches lockfile version 9.0 and CI)
RUN corepack enable && corepack prepare pnpm@9 --activate

# Install dependencies with layer caching
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile

# Copy source code and build config
COPY index.html ./
COPY tsconfig*.json ./
COPY vite.config.ts ./
COPY postcss.config.js ./
COPY tailwind.config.js ./
COPY src/ ./src/

# Accept build-time API base URL (defaults to localhost:8000 for browser access)
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# Build the production bundle
RUN pnpm run build

# ===========================================================================
# Stage 2: Serve the static SPA with Nginx
# ===========================================================================
FROM nginx:1.27-alpine AS runtime

# Copy custom Nginx configuration for SPA routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy compiled assets from the build stage
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# Health check
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
