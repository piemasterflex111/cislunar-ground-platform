#!/bin/bash
# setup.sh — EC2 user_data script
# Installs Docker, clones the repo, deploys via Docker Compose

set -e

# Update and install dependencies
apt-get update
apt-get install -y ca-certificates curl gnupg

# Install Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
usermod -aG docker ubuntu

# Clone the repository
cd /home/ubuntu
git clone https://github.com/payam-adloo/cislunar-ground-platform.git 2>/dev/null || \
  echo "Clone failed — will be pushed manually"
cd cislunar-ground-platform || mkdir cislunar-ground-platform

# Create default .env for the services
cat > .env << 'ENVFILE'
POSTGRES_USER=ground
POSTGRES_PASSWORD=change-me-production
POSTGRES_DB=ground
GRAFANA_ADMIN_PASSWORD=change-me-grafana
VLLM_BASE_URL=http://localhost:8001/v1
VLLM_MODEL=qwen3.6-27b-nvfp4-mtp
ENVFILE

# Deploy
docker compose -f deployment/compose.yaml up -d

# Open port
ufw allow 8080

# Done — this message appears in the instance console
echo "Cislunar Ground Platform deployed. API at http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8080/api/docs"