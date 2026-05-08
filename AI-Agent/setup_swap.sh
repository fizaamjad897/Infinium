#!/bin/bash
# setup_swap.sh — Emergency RAM for 1GB instances
set -e

echo "🚀 Setting up 4GB Swap Space..."

# 1. Create a 4GB swap file
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 2. Make it permanent
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3. Optimize swappiness (don't swap too aggressively, but allow it)
sudo sysctl vm.swappiness=10
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf

echo "✅ 4GB Swap Space is active! Your 1GB server now has 'virtual' breathing room."
free -h
