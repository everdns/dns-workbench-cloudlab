#!/bin/sh
sudo apt-get update -y
sudo apt-get -y install apt-transport-https ca-certificates wget
sudo wget -O /usr/share/keyrings/cznic-labs-pkg.gpg https://pkg.labs.nic.cz/gpg
echo "deb [signed-by=/usr/share/keyrings/cznic-labs-pkg.gpg] https://pkg.labs.nic.cz/knot-resolver jammy main" | sudo tee /etc/apt/sources.list.d/cznic-labs-knot-resolver.list 
sudo apt-get update -y
sudo systemctl mask knot-resolver
sudo apt-get install knot-resolver6 -y
sudo systemctl unmask knot-resolver