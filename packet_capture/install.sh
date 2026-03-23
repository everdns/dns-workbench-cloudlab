#!/bin/sh
# Install dependencies for eBPF-based DNS packet timestamping tool
sudo apt update -y
sudo apt install -y bpfcc-tools linux-headers-$(uname -r) python3-bpfcc
# Optional: numpy for the analysis script
sudo apt install -y python3-numpy