# Stage 1: Install dependencies and build
FROM node:20-alpine AS builder
WORKDIR /app
RUN npm install -g pnpm@9
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install
COPY . .
RUN pnpm run build

# Stage 2: Production server
FROM node:20-alpine
WORKDIR /app
RUN npm install -g pnpm@9
COPY --from=builder /app/.next ./.next
# `public/` is NOT carried by `next build` into .next, and this multi-stage
# build only copies .next forward, so without this line every file in public/
# 404s at runtime: favicon.ico, og-image.png, and the Onsite48 logo variants
# used by AuthScreen. Single-stage builds get this for free; two-stage does not.
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json /app/pnpm-lock.yaml ./
COPY --from=builder /app/node_modules ./node_modules
EXPOSE 3000
CMD ["pnpm", "start"]