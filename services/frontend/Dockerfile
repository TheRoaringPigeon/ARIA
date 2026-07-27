FROM node:22-slim AS base

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

FROM base AS dev
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]

# Vite inlines `import.meta.env.VITE_*` into the bundle at build time, so
# these have to be build args, not container `environment:` — setting them
# on the `prod` service in docker-compose.prod.yml has no effect once built.
FROM base AS build
ARG VITE_CORE_API_URL
ARG VITE_AI_SERVICE_URL
ENV VITE_CORE_API_URL=$VITE_CORE_API_URL
ENV VITE_AI_SERVICE_URL=$VITE_AI_SERVICE_URL
RUN npm run build

FROM node:22-slim AS prod
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
EXPOSE 4173
CMD ["serve", "-s", "dist", "-l", "4173"]
