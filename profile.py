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
pc.defineParameter("resolver_software", "Software To Use on Resolver", portal.ParameterType.STRING, "none", ["bind", "powerdns-recursor", "unbound", "knot-resolver", "all", "none"])
pc.defineParameter("name_server_software", "Software To Use on Name Server", portal.ParameterType.STRING, "none", ["bind", "powerdns-authoritative-server", "knotdns", "nsd", "unbound", "all", "none"])
pc.defineParameter("allow_interswitch_links", "Allow Interswitch Links", portal.ParameterType.BOOLEAN, False)
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
node_Resolver.addService(pg.Execute('/bin/sh','sudo apt update -y && sudo apt upgrade -y'))
node_Resolver.addService(pg.Execute('/bin/sh','sudo ufw allow 53/tcp && sudo ufw allow 53/udp && sudo ufw allow 853/tcp && sudo ufw allow 443/tcp'))

# Node NS_Local
node_NS_Local = request.RawPC('NS_Local')
node_NS_Local.addService(pg.Execute('/bin/sh','sudo apt update -y && sudo apt upgrade -y'))
node_NS_Local.addService(pg.Execute('/bin/sh','sudo ufw allow 53/tcp && sudo ufw allow 53/udp && sudo ufw allow 853/tcp && sudo ufw allow 443/tcp'))

#Network
main_link = request.Link('main_link')
if not params.allow_interswitch_links:
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
    if not params.allow_interswitch_links:
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

iface_flag = "true" if params.multiple_resolver_iface else "false"
#Bind Resolver
if params.resolver_software == "bind":
    node_Resolver.addService(pg.Execute('sh','/local/repository/bind/resolver/install.sh ' + iface_flag))
    node_Resolver.addService(pg.Execute('sh','/local/repository/bind/resolver/start.sh'))
#PowerDNS Resolver
elif params.resolver_software == "powerdns-recursor":
    node_Resolver.addService(pg.Execute('sh','/local/repository/powerdns/resolver/install.sh ' + iface_flag))
    node_Resolver.addService(pg.Execute('sh','/local/repository/powerdns/resolver/start.sh'))
#Knot Resolver
elif params.resolver_software == "knot-resolver":
    node_Resolver.addService(pg.Execute('sh','/local/repository/knot/resolver/install.sh ' + iface_flag))
    node_Resolver.addService(pg.Execute('sh','/local/repository/knot/resolver/start.sh'))
#Unbound Resolver
elif params.resolver_software == "unbound":
    node_Resolver.addService(pg.Execute('sh','/local/repository/unbound/resolver/install.sh ' + iface_flag))
    node_Resolver.addService(pg.Execute('sh','/local/repository/unbound/resolver/start.sh'))
#All Resolver Software (install only, no start)
elif params.resolver_software == "all":
    node_Resolver.addService(pg.Execute('sh','/local/repository/bind/resolver/install.sh ' + iface_flag))
    node_Resolver.addService(pg.Execute('sh','/local/repository/powerdns/resolver/install.sh ' + iface_flag))
    node_Resolver.addService(pg.Execute('sh','/local/repository/knot/resolver/install.sh ' + iface_flag))
    node_Resolver.addService(pg.Execute('sh','/local/repository/unbound/resolver/install.sh ' + iface_flag))
#None or unimplemented resolver software
else:
    node_Resolver.addService(pg.Execute('/bin/sh','echo "None selected or Resolver software installation not implemented yet" > /tmp/resolver_software_selection.txt'))

#Bind Name Server
if params.name_server_software == "bind":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/bind/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/bind/ns/start.sh'))
#PowerDNS Name Server
elif params.name_server_software == "powerdns-authoritative-server":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/powerdns/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/powerdns/ns/start.sh'))
#KnotDNS Name Server
elif params.name_server_software == "knotdns":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/knot/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/knot/ns/start.sh'))
#NSD Name Server
elif params.name_server_software == "nsd":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/nsd/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/nsd/ns/start.sh'))
#Unbound Name Server
elif params.name_server_software == "unbound":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/unbound/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/unbound/ns/start.sh'))
#All Name Server Software (install only, no start)
elif params.name_server_software == "all":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/bind/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/powerdns/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/knot/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/nsd/ns/install.sh ' + iface_flag))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/unbound/ns/install.sh ' + iface_flag))
#None or unimplemented name server software
else:
    node_NS_Local.addService(pg.Execute('/bin/sh','echo "None selected or Name Server software installation not implemented yet" > /tmp/name_server_software_selection.txt'))

#Try to install collectl for monitoring on both resolver and name server
node_NS_Local.addService(pg.Execute('sh','/local/repository/tool_install/install_collectl.sh'))
node_Resolver.addService(pg.Execute('sh','/local/repository/tool_install/install_collectl.sh'))

for i in range(params.num_testers):
    node = request.RawPC("test_host_" + str(i))
    node.addService(pg.Execute('sh','/local/repository/load_tester/install.sh'))
    iface = node.addInterface("interface-tester" + str(i), pg.IPv4Address(str(next_ip),TEST_HOST_SUBNET_MASK))
    next_ip += 1
    iface.bandwidth = 10000000
    main_link.addInterface(iface)

portal.context.printRequestRSpec()