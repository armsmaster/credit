# One Dockerfile for the shell and every microfrontend:  --build-arg APP=shell|mfe-admin|mfe-curves
# Each app is still an independently built and deployed image (own container behind the gateway).
ARG DOCKERHUB_MIRROR=docker.io

FROM ${DOCKERHUB_MIRROR}/library/node:22.12.0-alpine AS build
# npm mirror for the corporate contour; HTTP(S)_PROXY are Docker's predefined build args.
ARG NPM_REGISTRY=https://registry.npmjs.org/
ARG APP
WORKDIR /src
RUN npm config set registry "$NPM_REGISTRY" \
 && npm config set fund false \
 && npm config set audit false \
 && npm config set update-notifier false
COPY package.json package-lock.json ./
COPY packages/platform/package.json packages/platform/
COPY apps/shell/package.json apps/shell/
COPY apps/mfe-admin/package.json apps/mfe-admin/
COPY apps/mfe-curves/package.json apps/mfe-curves/
RUN npm ci
COPY . .
RUN test -n "$APP" && npm run build -w "apps/$APP"

FROM ${DOCKERHUB_MIRROR}/library/nginx:1.27.3-alpine
ARG APP
COPY --from=build /src/apps/${APP}/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/apps/${APP}/dist/ /usr/share/nginx/html/
EXPOSE 80
