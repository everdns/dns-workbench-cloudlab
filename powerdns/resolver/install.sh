echo "deb [signed-by=/etc/apt/keyrings/rec-54-pub.asc] http://repo.powerdns.com/ubuntu jammy-rec-54 main" | sudo tee /etc/apt/sources.list.d/pdns.list
printf "Package: pdns-*\nPin: origin repo.powerdns.com\nPin-Priority: 600\n" | sudo tee /etc/apt/preferences.d/rec-54 > /dev/null
sudo install -d /etc/apt/keyrings; curl https://repo.powerdns.com/FD380FBB-pub.asc | sudo tee /etc/apt/keyrings/rec-54-pub.asc
sudo apt-get update 
sudo systemctl mask pdns-recursor
sudo apt-get install pdns-recursor
sudo systemctl unmask pdns-recursor