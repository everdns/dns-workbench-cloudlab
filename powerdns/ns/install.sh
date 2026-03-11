#!/bin/sh
echo "deb [signed-by=/etc/apt/keyrings/auth-50-pub.asc] http://repo.powerdns.com/ubuntu jammy-auth-50 main" | sudo tee /etc/apt/sources.list.d/pdns.list
printf "Package: pdns-*\nPin: origin repo.powerdns.com\nPin-Priority: 600\n" | sudo tee /etc/apt/preferences.d/rec-54 > /dev/null
sudo install -d /etc/apt/keyrings; curl https://repo.powerdns.com/FD380FBB-pub.asc | sudo tee /etc/apt/keyrings/auth-50-pub.asc
sudo apt-get update 
sudo systemctl mask pdns
sudo apt-get install pdns-server -y
sudo systemctl unmask pdns