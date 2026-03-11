"""Simple Experiment with a user Host, Resolver, and Local Name Server"""
# Import the Portal object.
import geni.portal as portal
# Import the ProtoGENI library.
import geni.rspec.pg as pg
# Import the Emulab specific extensions.
import geni.rspec.emulab as emulab
import ipaddress

TEST_HOST_SUBNET_BASE_IP = ipaddress.IPv4Address(u"10.10.1.1")
next_ip = TEST_HOST_SUBNET_BASE_IP
TEST_HOST_SUBNET_MASK = "255.255.255.0"
AS_SUBNET_BASE_IP = ipaddress.IPv4Address(u"10.10.2.1")
AS_SUBNET_MASK = "255.255.255.0"

# Create a portal object,
pc = portal.Context()

# Describe the parameter(s) this profile script can accept.
pc.defineParameter("num_testers", "Number of Test VMs", portal.ParameterType.INTEGER, 1 )
pc.defineParameter("multiple_resolver_iface", "Seperate AS Interface", portal.ParameterType.BOOLEAN, False)
pc.defineParameter("resolver_software", "Software To Use on Resolver", portal.ParameterType.STRING, "none", ["bind", "powerdns", "unbound", "knotdns", "nsd", "none"])
pc.defineParameter("name_server_software", "Software To Use on Name Server", portal.ParameterType.STRING, "none", ["bind", "powerdns", "unbound", "knotdns", "nsd", "none"])

# Create a Request object to start building the RSpec.
request = pc.makeRequestRSpec()

# Retrieve the values the user specifies during instantiation.
params = portal.context.bindParameters()
if params.num_testers < 1 or params.num_testers > 200: 
    portal.context.reportError( portal.ParameterError( "You must choose at least 1 test VM and less than 200.", ["num_testers"] ) )

# Abort execution if there are any errors, and report them.
portal.context.verifyParameters()

# Node Resolver
node_Resolver = request.RawPC('Resolver')
resolver_base_ip = str(next_ip)
next_ip += 1
iface_resolver1 = node_Resolver.addInterface('interface-resolver1', pg.IPv4Address(resolver_base_ip,TEST_HOST_SUBNET_MASK))
node_Resolver.addService(pg.Execute('/bin/sh','sudo apt update && sudo apt upgrade -y'))
node_Resolver.addService(pg.Execute('/bin/sh','sudo ufw allow dns'))
node_Resolver.addService(pg.Execute('/bin/sh','sudo ufw allow 853/tcp && sudo ufw allow 443/tcp'))

# Node NS_Local
node_NS_Local = request.RawPC('NS_Local')
node_NS_Local.addService(pg.Execute('/bin/sh','sudo apt update && sudo apt upgrade -y'))
node_NS_Local.addService(pg.Execute('/bin/sh','sudo ufw allow dns'))
node_NS_Local.addService(pg.Execute('/bin/sh','sudo ufw allow 853/tcp && sudo ufw allow 443/tcp'))

#Network
main_link = request.Link('main_link')
main_link.setNoInterSwitchLinks()
main_link.Site('undefined')
iface_resolver1.bandwidth = 10000000
main_link.addInterface(iface_resolver1)

ns_ip = "10.10.2.2"
if params.multiple_resolver_iface:
    iface_resolver2 = node_Resolver.addInterface('interface-resolver2', pg.IPv4Address(str(AS_SUBNET_BASE_IP), AS_SUBNET_MASK))
    ns_ip = str(AS_SUBNET_BASE_IP + 1)
    iface_ns = node_NS_Local.addInterface('interface-ns', pg.IPv4Address(ns_ip, AS_SUBNET_MASK))
    link_ns = request.Link('link_ns')
    link_ns.setNoInterSwitchLinks()
    link_ns.Site('undefined')
    iface_resolver2.bandwidth = 10000000
    link_ns.addInterface(iface_resolver2)
    iface_ns.bandwidth = 10000000
    link_ns.addInterface(iface_ns)
else:
    ns_ip = str(next_ip)
    iface_ns = node_NS_Local.addInterface('interface-ns', pg.IPv4Address(str(next_ip),TEST_HOST_SUBNET_MASK))
    next_ip += 1
    iface_ns.bandwidth = 10000000
    main_link.addInterface(iface_ns)

#Bind Resolver
if params.resolver_software == "bind":
    node_Resolver.addService(pg.Execute('sh','/local/repository/bind/install.sh'))
    node_Resolver.addService(pg.Execute('/bin/sh','sudo cp /local/repository/bind/resolver/named.conf.options /etc/bind/named.conf.options'))
    if params.multiple_resolver_iface:
        #copy bind files resolver
        node_Resolver.addService(pg.Execute('/bin/sh','sudo cp /local/repository/bind/resolver/named.conf.local2 /etc/bind/named.conf.local'))
    else:
        #copy bind files resolver
        node_Resolver.addService(pg.Execute('/bin/sh','sudo cp /local/repository/bind/resolver/named.conf.local /etc/bind/named.conf.local'))
    node_Resolver.addService(pg.Execute('/bin/sh','sudo systemctl enable bind9 && sudo systemctl restart bind9'))
#PowerDNS Resolver
elif params.resolver_software == "powerdns":
    node_Resolver.addService(pg.Execute('sh','/local/repository/powerdns/resolver/install.sh'))
    if params.multiple_resolver_iface:
        node_Resolver.addService(pg.Execute('/bin/sh','sudo cp /local/repository/powerdns/resolver/recursor2.conf /etc/powerdns/recursor.conf'))
    else:
        node_Resolver.addService(pg.Execute('/bin/sh','sudo cp /local/repository/powerdns/resolver/recursor.conf /etc/powerdns/recursor.conf'))
    node_Resolver.addService(pg.Execute('/bin/sh','sudo systemctl enable pdns-recursor && sudo systemctl start pdns-recursor'))

#None or unimplemented resolver software
else:
    node_Resolver.addService(pg.Execute('/bin/sh','sudo echo "None selected or Resolver software installation not implemented yet" > /tmp/resolver_software_selection.txt'))

if params.name_server_software == "bind":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/bind/install.sh'))
    node_NS_Local.addService(pg.Execute('/bin/sh','sudo cp /local/repository/bind/ns/named.conf.local /etc/bind/named.conf.local'))
    if params.multiple_resolver_iface:
        #copy bind files name server
        node_NS_Local.addService(pg.Execute('/bin/sh','sudo cp /local/repository/bind/ns/named.conf.options2 /etc/bind/named.conf.options'))
    else:
        #copy bind files name server
        node_NS_Local.addService(pg.Execute('/bin/sh','sudo cp /local/repository/bind/ns/named.conf.options /etc/bind/named.conf.options'))
    node_NS_Local.addService(pg.Execute('/bin/sh','sudo systemctl enable bind9 && sudo systemctl restart bind9'))
else:
    node_NS_Local.addService(pg.Execute('/bin/sh','sudo echo "None selected or Name Server software installation not implemented yet" > /tmp/name_server_software_selection.txt'))

#Try to install collectl for monitoring on both resolver and name server
node_NS_Local.addService(pg.Execute('sh','/local/repository/install_collectl.sh'))
node_Resolver.addService(pg.Execute('sh','/local/repository/install_collectl.sh'))

for i in range(params.num_testers):
    node = request.RawPC("test_host_" + str(i))
    node.addService(pg.Execute('/bin/sh','echo \'export PATH=$PATH:/opt/go/bin\' | sudo tee /etc/profile.d/go_path.sh && sudo chmod +x /etc/profile.d/go_path.sh'))
    node.addService(pg.Execute('/bin/sh','sudo add-apt-repository ppa:longsleep/golang-backports'))
    node.addService(pg.Execute('/bin/sh','sudo apt update && sudo apt upgrade -y'))
    node.addService(pg.Execute('/bin/sh','sudo apt install golang -y'))
    node.addService(pg.Execute('/bin/sh','sudo mkdir -p /opt/go && sudo chown -R $USER /opt/go'))
    node.addService(pg.Execute('/bin/sh','GOPATH=/opt/go PATH=$PATH:/opt/go/bin go install github.com/tantalor93/dnspyre/v3@latest'))
    node.addService(pg.Execute('/bin/sh','GOPATH=/opt/go PATH=$PATH:/opt/go/bin go install github.com/everdns/dnspyre-dnsworkbench@latest'))
    node.addService(pg.Execute('/bin/sh','sudo apt install -y autoconf automake libtool  libssl-dev libldns-dev libck-dev libnghttp2-dev'))
    node.addService(pg.Execute('/bin/sh','sudo git clone https://codeberg.org/DNS-OARC/dnsperf.git /opt/dnsperf'))
    node.addService(pg.Execute('/bin/sh','cd /opt/dnsperf && sudo ./autogen.sh && sudo ./configure'))
    node.addService(pg.Execute('/bin/sh','cd /opt/dnsperf && sudo make && sudo make install'))
    node.addService(pg.Execute('/bin/sh','sudo git clone https://github.com/everdns/dnsperf-dnsworkbench.git /opt/dnsperf-dnsworkbench'))
    node.addService(pg.Execute('/bin/sh','cd /opt/dnsperf-dnsworkbench && sudo ./autogen.sh && sudo ./configure'))
    node.addService(pg.Execute('/bin/sh','cd /opt/dnsperf-dnsworkbench && sudo make && sudo make install'))
    node.addService(pg.Execute('/bin/sh','sudo apt install -y gcc g++'))
    node.addService(pg.Execute('/bin/sh','sudo apt install -y clang'))
    node.addService(pg.Execute('/bin/sh','sudo git clone https://github.com/everdns/dns64perfpp-dnsworkbench.git /opt/dns64perfpp-dnsworkbench'))
    node.addService(pg.Execute('/bin/sh','cd /opt/dns64perfpp-dnsworkbench && sudo make CXXFLAGS+=" -DDNS64PERFPP_IPV4" && sudo make install'))
    node.addService(pg.Execute('/bin/sh','sudo git clone https://github.com/everdns/dns64perfpp-dnsworkbench.git /opt/dns64perfpp'))
    node.addService(pg.Execute('/bin/sh','cd /opt/dns64perfpp && sudo git checkout original_feature/multiport'))
    node.addService(pg.Execute('/bin/sh','cd /opt/dns64perfpp && sudo make CXXFLAGS+=" -DDNS64PERFPP_IPV4" && sudo make install'))
    iface = node.addInterface("interface-tester" + str(i), pg.IPv4Address(str(next_ip),TEST_HOST_SUBNET_MASK))
    next_ip += 1
    iface.bandwidth = 10000000
    main_link.addInterface(iface)

portal.context.printRequestRSpec()