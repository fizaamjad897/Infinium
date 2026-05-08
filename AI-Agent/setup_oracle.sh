#!/bin/bash
set -e

echo "🚀 Starting Oracle Cloud Setup for Infinium AI Agent..."

# Detect Package Manager
if command -v dnf &> /dev/null; then
    PM="dnf"
elif command -v apt-get &> /dev/null; then
    PM="apt"
else
    echo "❌ Error: Could not detect package manager (dnf or apt)."
    exit 1
fi

echo "📦 Using package manager: $PM"

# 1. Update and install basics
if [ "$PM" == "dnf" ]; then
    sudo dnf update -y
    sudo dnf install -y curl git
else
    sudo apt-get update
    sudo apt-get install -y apt-transport-https ca-certificates curl software-properties-common git
fi

# 2. Install Docker
if ! command -v docker &> /dev/null; then
    echo "🐳 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo systemctl enable --now docker
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
if command -v firewall-cmd &> /dev/null; then
    sudo firewall-cmd --permanent --add-port=8000/tcp
    sudo firewall-cmd --reload
elif command -v iptables &> /dev/null; then
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
    if command -v netfilter-persistent &> /dev/null; then
        sudo netfilter-persistent save
    fi
fi

echo "✅ Firewall rules updated!"

# 5. Check Architecture
ARCH=$(uname -m)
echo "🧬 System Architecture: $ARCH"

echo "🎉 Oracle Server preparation complete!"
echo "⚠️ IMPORTANT: Please log out and log back in (type 'exit') to apply Docker permissions."
