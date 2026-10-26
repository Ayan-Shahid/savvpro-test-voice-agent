# syntax=docker/dockerfile:1.12
# Targets: dev (next dev), build, prod (standalone server, non-root). No secrets are baked in: the image
# reads PUBLIC_ORIGIN and INTERNAL_ORIGIN at runtime, so one image serves every environment.
ARG NODE=26.12.1
FROM node:${NODE}-alpine AS base
ENV NEXT_TELEMETRY_DISABLED=1
RUN apk add --no-cache tini && addgroup -S -g 10001 astro && adduser -S -u 10001 -G astro astro
WORKDIR /app

FROM base AS deps
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

FROM deps AS dev
COPY --chown=astro:astro . .
# next dev writes .next/ and next-env.d.ts, so the app directory must belong to the runtime user.
RUN mkdir -p .next && chown astro:astro /app /app/.next
USER astro
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

FROM deps AS build
COPY . .
RUN npm run lint && npm run typecheck && npm test && npm run build

FROM base AS prod
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=astro:astro /app/.next/standalone ./
COPY --from=build --chown=astro:astro /app/.next/static ./.next/static
COPY --from=build --chown=astro:astro /app/public ./public
USER astro
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:3000/robots.txt >/dev/null || exit 1
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "server.js"]
