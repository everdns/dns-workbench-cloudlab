#!/bin/sh
# Stop and uninstall NSD (authoritative name server, compiled from source)
/local/repository/ns_software/nsd/stop.sh

# Remove nsd files installed by nsd/ns/install.sh
sudo make -C /opt/nsd-4.14.1 uninstall 2>/dev/null
sudo rm -f /usr/local/sbin/nsd /usr/local/sbin/nsd-checkconf /usr/local/sbin/nsd-checkzone /usr/local/sbin/nsd-control
sudo rm -rf /opt/nsd-4.14.1.tar.gz /opt/nsd-4.14.1 /etc/nsd /var/run /var/db/nsd/xfrd.state /var/db/nsd/zone.list /var/db/nsd/cookiesecrets.txt
