# Tiny cron job that wakes the sleeping backend just before its timed jobs.
# Railway runs this container on the service's cron schedule; it pings and exits.
FROM alpine:3.20
RUN apk add --no-cache curl
COPY ping.sh /ping.sh
CMD ["/bin/sh", "/ping.sh"]
