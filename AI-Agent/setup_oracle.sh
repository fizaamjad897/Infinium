#!/bin/bash
set -e

echo "🚀 Starting Oracle Cloud ARM Setup for Infinium AI Agent..."

# 1. Update and install basics
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl software-properties-common git

# 2. Install Docker
if ! command -v docker &> /dev/null; then
    echo "🐳 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
fi

# 3. Install Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo "🐙 Installing Docker Compose..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# 4. Open Internal Firewall (Oracle is strict!)
echo "🛡️ Opening Internal Firewall (Port 8000)..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save

echo "✅ Firewall rules updated!"

# 5. Check Architecture
ARCH=$(uname -m)
echo "🧬 System Architecture: $ARCH"
if [ "$ARCH" != "aarch64" ]; then
    echo "⚠️ Warning: You are not on an ARM instance. Ensure your Docker images match."
fi

echo "🎉 Oracle Server preparation complete! Please log out and log back in to use Docker."
