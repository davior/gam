# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# `npm run build` is `tsc --noEmit && vite build`, so a type error fails the image
# rather than shipping.
RUN npm run build

# Production stage
FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 80

ENV UPLOAD_MAX_SIZE=4g
# The suite where people sign in, as browsers reach it, for the CSP's connect-src. Empty:
# SUITE_BASE_URL. Substituted at container start, not build time.
ENV SUITE_PUBLIC_URL=
# Also in connect-src, for the theme picker's calls to Gecko Suite (M7).
ENV SUITE_BASE_URL=https://suite.geckopico.com

# Only these are substituted; nginx's own $variables ($uri, $host, $remote_addr …)
# must survive untouched, which is why envsubst is given an explicit list rather than
# left to replace everything it recognises.
# SUITE_WS_URL is SUITE_BASE_URL as a websocket address (https -> wss), for live dictation's
# stream. Derived here so there is still only one setting to get right.
CMD ["/bin/sh", "-c", "export SIGN_IN_URL=\"${SUITE_PUBLIC_URL:-$SUITE_BASE_URL}\" && export SUITE_WS_URL=$(echo \"$SUITE_BASE_URL\" | sed 's#^http#ws#') && envsubst '${UPLOAD_MAX_SIZE} ${SIGN_IN_URL} ${SUITE_BASE_URL} ${SUITE_WS_URL}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
