FROM alpine:3.20

# restic lives in the community repo, which isn't reliably enabled by default
# in every Alpine base image — add it explicitly rather than assume.
RUN echo "https://dl-cdn.alpinelinux.org/alpine/v3.20/community" >> /etc/apk/repositories \
    && apk update \
    && apk add --no-cache restic sqlite openssh-client tzdata ca-certificates

COPY entrypoint.sh /entrypoint.sh
COPY backup.sh /usr/local/bin/backup.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/backup.sh

WORKDIR /backup
ENTRYPOINT ["/entrypoint.sh"]
