#!/bin/bash
# Industrial Data Platform — nginx vhost setup
# Run as root on the VPS: bash nginx/setup.sh

set -e

DOMAIN="techflow24.com"
EMAIL="info@techflow24.com"

echo "=== Setting up nginx vhosts for IDP ==="

# Grafana → port 3000
cat > /etc/nginx/sites-available/grafana.$DOMAIN << 'EOF'
server {
    server_name grafana.techflow24.com;
    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 50M;
    }
    listen 80;
}
EOF

# FastAPI → port 8001
cat > /etc/nginx/sites-available/api.$DOMAIN << 'EOF'
server {
    server_name api.techflow24.com;
    location / {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10M;
    }
    listen 80;
}
EOF

# RabbitMQ management → port 15673 (existing container)
cat > /etc/nginx/sites-available/rabbitmq.$DOMAIN << 'EOF'
server {
    server_name rabbitmq.techflow24.com;
    location / {
        proxy_pass http://localhost:15673;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10M;
    }
    listen 80;
}
EOF

# Portainer → port 9000 (existing container)
cat > /etc/nginx/sites-available/portainer.$DOMAIN << 'EOF'
server {
    server_name portainer.techflow24.com;
    location / {
        proxy_pass http://localhost:9000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10M;
    }
    listen 80;
}
EOF

# N8N IDP → port 5679 (new container, existing N8N is on 5678)
cat > /etc/nginx/sites-available/n8n.$DOMAIN << 'EOF'
server {
    server_name n8n.techflow24.com;
    location / {
        proxy_pass http://localhost:5679;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 50M;
    }
    listen 80;
}
EOF

# Enable all vhosts
for service in grafana api rabbitmq portainer n8n; do
    ln -sf /etc/nginx/sites-available/$service.$DOMAIN /etc/nginx/sites-enabled/$service.$DOMAIN
    echo "✓ Enabled $service.$DOMAIN"
done

# Test and reload nginx
nginx -t && nginx -s reload
echo "✓ nginx reloaded"

# Get SSL certificates for all new domains
echo "=== Requesting SSL certificates ==="
certbot --nginx \
    -d grafana.$DOMAIN \
    -d api.$DOMAIN \
    -d rabbitmq.$DOMAIN \
    -d portainer.$DOMAIN \
    -d n8n.$DOMAIN \
    --non-interactive \
    --agree-tos \
    -m $EMAIL

echo ""
echo "=== Done! SSL certificates installed ==="
echo "Now start the IDP stack: docker compose -f docker-compose.idp.yml up -d"
