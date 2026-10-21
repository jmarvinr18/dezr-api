#!/bin/bash
# Deploys the dezr API stack on Amazon Linux 2023: Docker, the PostgreSQL disk, the app bundle
# and its secrets, then `docker compose up`. Run by user data on first boot. Safe to run again
# (sudo /opt/dezr/deploy.sh), e.g. to restart everything after editing /opt/dezr/deploy.env.
set -euo pipefail
# shellcheck source=/dev/null
source /opt/dezr/deploy.env

APP_DIR=/opt/dezr/app
DATA_DIR=/srv/dezr-data
PLUGINS=/usr/local/lib/docker/cli-plugins

log() { echo "[dezr-deploy $(date -u +%H:%M:%S)] $*"; }

imds() {
  local token
  token=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
  curl -fsS -H "X-aws-ec2-metadata-token: $token" "http://169.254.169.254/latest/meta-data/$1"
}

param() {
  aws ssm get-parameter --region "$AWS_REGION" --name "$PARAM_PREFIX/$1" --with-decryption \
    --query Parameter.Value --output text
}

# ---------- 1. Internet access ----------
# The instance has no public address until Terraform attaches the Elastic IP.
log "Waiting for the Elastic IP $PUBLIC_IP"
for _ in $(seq 1 120); do
  [ "$(imds public-ipv4 2>/dev/null || true)" = "$PUBLIC_IP" ] && break
  sleep 5
done

# ---------- 2. Swap: headroom for image builds and large PDFs on a 2 GiB instance ----------
if [ ! -f /swapfile ]; then
  log "Adding 2 GiB of swap"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi
swapon --show | grep -q /swapfile || swapon /swapfile

# ---------- 3. Docker with the Compose and Buildx plugins ----------
if ! command -v docker > /dev/null || ! command -v unzip > /dev/null; then
  log "Installing Docker"
  dnf install -y docker unzip
fi
case "$(uname -m)" in
  aarch64) COMPOSE_ARCH=aarch64; BUILDX_ARCH=arm64 ;;
  x86_64) COMPOSE_ARCH=x86_64; BUILDX_ARCH=amd64 ;;
  *) echo "Unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac
mkdir -p "$PLUGINS"
if [ ! -x "$PLUGINS/docker-compose" ]; then
  log "Installing Docker Compose $COMPOSE_VERSION"
  curl -fsSL --retry 5 -o "$PLUGINS/docker-compose" \
    "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-$COMPOSE_ARCH"
  chmod +x "$PLUGINS/docker-compose"
fi
if [ ! -x "$PLUGINS/docker-buildx" ]; then
  log "Installing Docker Buildx $BUILDX_VERSION"
  curl -fsSL --retry 5 -o "$PLUGINS/docker-buildx" \
    "https://github.com/docker/buildx/releases/download/$BUILDX_VERSION/buildx-$BUILDX_VERSION.linux-$BUILDX_ARCH"
  chmod +x "$PLUGINS/docker-buildx"
fi
systemctl enable --now docker

# ---------- 4. The PostgreSQL disk (kept across redeploys) ----------
DEVICE="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME_ID//-/}"
log "Waiting for the data volume $DATA_VOLUME_ID"
for _ in $(seq 1 120); do
  [ -e "$DEVICE" ] && break
  [ -e /dev/xvdf ] && DEVICE=/dev/xvdf && break
  sleep 5
done
[ -e "$DEVICE" ] || { echo "The data volume never appeared." >&2; exit 1; }
DEVICE=$(readlink -f "$DEVICE")
# Format only a brand-new disk; an existing one holds the database.
if ! blkid "$DEVICE" > /dev/null 2>&1; then
  log "Formatting the new data volume"
  mkfs -t xfs "$DEVICE"
fi
UUID=$(blkid -s UUID -o value "$DEVICE")
mkdir -p "$DATA_DIR"
grep -q "$UUID" /etc/fstab || echo "UUID=$UUID $DATA_DIR xfs defaults,nofail 0 2" >> /etc/fstab
mountpoint -q "$DATA_DIR" || mount "$DATA_DIR"
mkdir -p "$DATA_DIR/postgres"
# After a reboot, Docker (and so PostgreSQL) waits for this disk; it must never start on an
# empty directory of the root disk instead.
mkdir -p /etc/systemd/system/docker.service.d
printf '[Unit]\nRequiresMountsFor=%s\n' "$DATA_DIR" > /etc/systemd/system/docker.service.d/dezr-data.conf
systemctl daemon-reload

# ---------- 5. The app and its settings ----------
log "Fetching s3://$BUCKET/$BUNDLE_KEY"
aws s3 cp "s3://$BUCKET/$BUNDLE_KEY" /opt/dezr/bundle.zip --region "$AWS_REGION" --only-show-errors
rm -rf "$APP_DIR.new"
mkdir -p "$APP_DIR.new"
unzip -q /opt/dezr/bundle.zip -d "$APP_DIR.new"
if [ -d "$APP_DIR" ]; then
  (cd "$APP_DIR" && docker compose down --remove-orphans) || true
  rm -rf "$APP_DIR"
fi
mv "$APP_DIR.new" "$APP_DIR"

log "Reading secrets from SSM Parameter Store"
POSTGRES_PASSWORD=$(param postgres-password)
SECRET_KEY=$(param secret-key)
DOCUMENT_SEARCH_API_KEY=$(param document-search-key)
# Bearer key of the AI chat endpoints (dezr-ui's NUXT_CHAT_API_TOKEN); empty leaves them open.
AI_API_KEY=""
if [ "$AI_REQUIRE_KEY" = "1" ]; then
  AI_API_KEY=$(param ai-api-key)
fi
# The default admin: created by the API container on start while there is no admin yet.
ADMIN_PASSWORD=""
if [ -n "$ADMIN_EMAIL" ]; then
  ADMIN_PASSWORD=$(param admin-password)
fi

umask 077
cat > "$APP_DIR/.env" <<EOF
POSTGRES_USER=dezr
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_DB=dezr
# PostgreSQL is reachable from this machine only; the API is on port 80.
POSTGRES_HOST_PORT=127.0.0.1:5435
API_HOST_PORT=80
SECRET_KEY=$SECRET_KEY
CORS_ORIGINS=$CORS_ORIGINS
TRUSTED_PROXY_COUNT=$TRUSTED_PROXY_COUNT
STORAGE_BACKEND=s3
S3_BUCKET=$BUCKET
S3_REGION=$AWS_REGION
S3_KEY_PREFIX=$MEDIA_PREFIX
EMBEDDING_PROVIDER=$EMBEDDING_PROVIDER
EMBEDDING_MODEL_ID=$EMBEDDING_MODEL_ID
EMBEDDING_REGION=$EMBEDDING_REGION
DOCUMENT_SEARCH_API_KEY=$DOCUMENT_SEARCH_API_KEY
# The AI assistant: dezr-ai's AgentCore runtime, reached with the instance role.
AI_AGENT_RUNTIME_ARN=$AI_AGENT_RUNTIME_ARN
AI_AGENT_REGION=$AI_AGENT_REGION
AI_API_KEY=$AI_API_KEY
AI_RATE_LIMIT_PER_MINUTE=$AI_RATE_LIMIT_PER_MINUTE
AI_DAILY_MESSAGE_LIMIT=$AI_DAILY_MESSAGE_LIMIT
DEFAULT_ADMIN_EMAIL='$ADMIN_EMAIL'
DEFAULT_ADMIN_PASSWORD='$ADMIN_PASSWORD'
EOF
umask 022

# The database files go on the data volume instead of a Docker volume on the root disk.
cat > "$APP_DIR/docker-compose.override.yml" <<EOF
services:
  db:
    volumes:
      - $DATA_DIR/postgres:/var/lib/postgresql/data
EOF

# ---------- 6. Build and start ----------
cd "$APP_DIR"
log "Building the API image (a few minutes on first boot)"
docker compose build api
log "Starting PostgreSQL, the API and the document worker"
docker compose up -d --remove-orphans

log "Waiting for the API to answer"
for _ in $(seq 1 90); do
  curl -fsS --max-time 5 http://127.0.0.1/healthz > /dev/null 2>&1 && break
  sleep 5
done
curl -fsS --max-time 5 http://127.0.0.1/healthz > /dev/null || { docker compose logs --tail 50 api >&2; exit 1; }

# ---------- 7. Default admin ----------
# The API container created it on start (flask admin ensure-default) if there was no admin yet.
if [ -n "$ADMIN_EMAIL" ]; then
  ADMINS=$(docker compose exec -T api flask admin list)
  if grep -qiF "$ADMIN_EMAIL " <<< "$ADMINS"; then
    log "Default admin: $ADMIN_EMAIL (password: terraform output -raw admin_password)"
  else
    log "Admins: $(awk '{print $1}' <<< "$ADMINS" | paste -sd, -) (the default admin is only created on an empty database)"
  fi
fi

log "Done: http://$PUBLIC_IP/"
