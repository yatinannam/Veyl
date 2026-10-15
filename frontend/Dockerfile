FROM node:24-alpine AS base
RUN npm install -g pnpm@11.9.0
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S veyl && adduser -S veyl -G veyl
COPY --from=build --chown=veyl:veyl /app/.next/standalone ./
COPY --from=build --chown=veyl:veyl /app/.next/static ./.next/static
COPY --from=build --chown=veyl:veyl /app/public ./public
USER veyl
EXPOSE 3000
CMD ["node", "server.js"]
