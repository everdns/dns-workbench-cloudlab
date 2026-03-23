#!/bin/sh
sudo git clone https://github.com/xdp-project/xdp-tools.git /opt/xdp-tools
cd /opt/xdp-tools
sudo git submodule init && sudo git submodule
sudo ./configure
sudo make
sudo make install