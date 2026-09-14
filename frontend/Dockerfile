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
# The CSP's connect-src, so a deployment pointing at a different Notes instance does
# not need its own image. Substituted at container start, not build time.
ENV NOTES_BASE_URL=https://notes.geckopico.com

# Only these two are substituted; nginx's own $variables ($uri, $host, $remote_addr …)
# must survive untouched, which is why envsubst is given an explicit list rather than
# left to replace everything it recognises.
CMD ["/bin/sh", "-c", "envsubst '${UPLOAD_MAX_SIZE} ${NOTES_BASE_URL}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
