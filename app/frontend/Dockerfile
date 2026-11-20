# TaskBoard frontend image
# nginx-unprivileged runs as a non-root user and listens on port 8080.
FROM nginxinc/nginx-unprivileged:1.29-alpine

# Where /api calls go. Docker Compose uses the service name "backend".
# In Kubernetes this is overridden with the backend Service DNS name.
ENV BACKEND_URL=http://backend:5000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

# Nginx config template (envsubst fills the variables at start-up)
COPY default.conf.template /etc/nginx/templates/default.conf.template

# Static site
COPY index.html styles.css app.js /usr/share/nginx/html/

EXPOSE 8080
