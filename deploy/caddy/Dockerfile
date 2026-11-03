FROM caddy:2.11.7
RUN addgroup -g 1000 caddy && adduser -D -u 1000 -G caddy caddy \
    && mkdir -p /data/caddy /config/caddy && chown -R caddy:caddy /data /config
USER caddy
