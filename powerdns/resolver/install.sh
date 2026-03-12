#!/bin/sh
MULTIPLE_IFACE="${1:-false}"
echo "deb [signed-by=/etc/apt/keyrings/rec-54-pub.asc] http://repo.powerdns.com/ubuntu jammy-rec-54 main" | sudo tee /etc/apt/sources.list.d/pdns.list
printf "Package: pdns-*\nPin: origin repo.powerdns.com\nPin-Priority: 600\n" | sudo tee /etc/apt/preferences.d/rec-54 > /dev/null
sudo install -d /etc/apt/keyrings; curl https://repo.powerdns.com/FD380FBB-pub.asc | sudo tee /etc/apt/keyrings/rec-54-pub.asc
sudo apt-get update
sudo systemctl mask pdns-recursor
sudo apt-get install pdns-recursor -y
if [ "$MULTIPLE_IFACE" = true ]; then
    sudo cp /local/repository/powerdns/resolver/recursor2.conf /etc/powerdns/recursor.conf
else
    sudo cp /local/repository/powerdns/resolver/recursor.conf /etc/powerdns/recursor.conf
fi
sudo systemctl unmask pdns-recursor
sudo systemctl enable pdns-recursor && sudo systemctl start pdns-recursor