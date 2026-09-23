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

# Create a portal object,
pc = portal.Context()

# Describe the parameter(s) this profile script can accept.
pc.defineParameter("num_testers", "Number of Test VMs", portal.ParameterType.INTEGER, 1 )
pc.defineParameter("name_server_software", "Software To Use on Name Server", portal.ParameterType.STRING, "none", ["bind", "powerdns-authoritative-server", "knotdns", "nsd", "unbound", "all", "none"])
pc.defineParameter("name_server_hardware", "Hardware for Name Server", portal.ParameterType.STRING, "any")
pc.defineParameter("tester_hardware", "Hardware for test hosts", portal.ParameterType.STRING, "any")
pc.defineParameter("allow_interswitch_links", "Allow Interswitch Links", portal.ParameterType.BOOLEAN, False)
# Create a Request object to start building the RSpec.
request = pc.makeRequestRSpec()

# Retrieve the values the user specifies during instantiation.
params = portal.context.bindParameters()
if params.num_testers < 1 or params.num_testers > 200: 
    portal.context.reportError( portal.ParameterError( "You must choose at least 1 test VM and less than 200.", ["num_testers"] ) )

# Abort execution if there are any errors, and report them.
portal.context.verifyParameters()

#Network
main_link = request.Link('main_link')
if not params.allow_interswitch_links:
    main_link.setNoInterSwitchLinks()
main_link.Site('undefined')

# Node NS_Local
node_NS_Local = request.RawPC('NS_Local')
if params.name_server_hardware != "any":
    node_NS_Local.hardware_type = params.name_server_hardware
node_NS_Local.addService(pg.Execute('/bin/sh','sudo apt update -y && sudo apt upgrade -y'))
node_NS_Local.addService(pg.Execute('/bin/sh','sudo ufw allow 53/tcp && sudo ufw allow 53/udp && sudo ufw allow 853/tcp && sudo ufw allow 443/tcp'))
ns_ip = str(next_ip)
iface_ns = node_NS_Local.addInterface('interface-ns', pg.IPv4Address(str(next_ip),TEST_HOST_SUBNET_MASK))
next_ip += 1
iface_ns.bandwidth = 10000000
main_link.addInterface(iface_ns)   

#Bind Name Server
if params.name_server_software == "bind":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/bind/install.sh '))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/bind/start.sh'))
#PowerDNS Name Server
elif params.name_server_software == "powerdns-authoritative-server":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/powerdns/install.sh '))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/powerdns/start.sh'))
#KnotDNS Name Server
elif params.name_server_software == "knotdns":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/knot/install.sh '))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/knot/start.sh'))
#NSD Name Server
elif params.name_server_software == "nsd":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/nsd/install.sh '))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/nsd/start.sh'))
#Unbound Name Server
elif params.name_server_software == "unbound":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/unbound/install.sh '))
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/unbound/start.sh'))
#All Name Server Software (install only, no start)
elif params.name_server_software == "all":
    node_NS_Local.addService(pg.Execute('sh','/local/repository/ns_software/bind/install_all_ns.sh'))
#None or unimplemented name server software
else:
    node_NS_Local.addService(pg.Execute('/bin/sh','echo "None selected or Name Server software installation not implemented yet" > /tmp/name_server_software_selection.txt'))

#Try to install collectl for monitoring on both resolver and name server
node_NS_Local.addService(pg.Execute('sh','/local/repository/tool_install/install_collectl.sh'))

for i in range(params.num_testers):
    node = request.RawPC("test_host_" + str(i))
    if params.tester_hardware != "any":
        node.hardware_type = params.tester_hardware
    node.addService(pg.Execute('sh','/local/repository/load_tester/install.sh'))
    iface = node.addInterface("interface-tester" + str(i), pg.IPv4Address(str(next_ip),TEST_HOST_SUBNET_MASK))
    next_ip += 1
    iface.bandwidth = 10000000
    main_link.addInterface(iface)

portal.context.printRequestRSpec()