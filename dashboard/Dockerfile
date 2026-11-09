# Multi-stage production build for Relay Next.js Dashboard
FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG NEXT_PUBLIC_GATEWAY_URL=http://localhost:8000
ARG NEXT_PUBLIC_ADMIN_KEY=relay_admin_secret_key_12345
ENV NEXT_PUBLIC_GATEWAY_URL=$NEXT_PUBLIC_GATEWAY_URL
ENV NEXT_PUBLIC_ADMIN_KEY=$NEXT_PUBLIC_ADMIN_KEY

RUN npm run build

# Production runner stage
FROM node:22-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/src ./src

EXPOSE 3000

CMD ["npm", "run", "start"]

