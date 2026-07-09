#!/bin/bash
# deploy.sh — Deploy THermes to Digital Ocean VPS
# Usage: ./deploy.sh <vps-ip>

VPS_IP="${1:?Usage: ./deploy.sh <vps-ip>}"
SSH_USER="root"

echo "🚀 Deploying THermes to $VPS_IP..."

# 1. Push latest commits
echo "📤 Pushing to GitHub..."
git push origin main || { echo "❌ Push failed"; exit 1; }

# 2. SSH into VPS, pull, and restart
echo "🔁 Updating VPS..."
ssh ${SSH_USER}@${VPS_IP} << 'DEPLOY'
  set -e
  echo "→ Pulling THermes..."
  cd /opt/thermes
  git pull origin main

  echo "→ Restarting THermes..."
  systemctl restart thermes

  echo "→ Checking health..."
  sleep 2
  curl -s http://localhost:8788/api/health | python3 -m json.tool

  echo ""
  echo "✅ Deploy complete!"
  echo "THermes:    http://$(curl -s ifconfig.me):8788"
DEPLOY

echo ""
echo "✅ Done! http://$VPS_IP:8788"
