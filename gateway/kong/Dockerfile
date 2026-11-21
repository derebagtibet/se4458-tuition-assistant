FROM kong:3.9
USER root
COPY kong.yml.tpl /kong/kong.yml.tpl
COPY render-and-start.sh /kong/render-and-start.sh
RUN chmod 0755 /kong/render-and-start.sh && chown -R kong /kong
USER kong
ENV KONG_DATABASE=off \
    KONG_PROXY_LISTEN="0.0.0.0:8000" \
    KONG_ADMIN_LISTEN="127.0.0.1:8444" \
    KONG_UNTRUSTED_LUA=on \
    KONG_PROXY_ACCESS_LOG=/dev/stdout \
    KONG_PROXY_ERROR_LOG=/dev/stderr \
    KONG_ADMIN_ACCESS_LOG=/dev/stdout \
    KONG_ADMIN_ERROR_LOG=/dev/stderr
EXPOSE 8000
ENTRYPOINT ["/kong/render-and-start.sh"]
