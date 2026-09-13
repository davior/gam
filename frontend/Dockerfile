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

# Only UPLOAD_MAX_SIZE is substituted; nginx's own $variables must survive untouched,
# which is why envsubst is given an explicit list rather than left to replace them all.
CMD ["/bin/sh", "-c", "envsubst '${UPLOAD_MAX_SIZE}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
