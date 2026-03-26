#include "dns.h"
#include "net.h"

#include <arpa/inet.h>
#include <string.h>

struct dns_template dns_templates[DNS_NUM_TEMPLATES];

/*
 * Build a pre-serialized answer RR.
 * All answer RRs use name compression pointer 0xC00C (offset 12 = QNAME in question).
 *
 * Wire format:
 *   NAME (2 bytes, compression pointer)
 *   TYPE (2 bytes)
 *   CLASS (2 bytes, IN=1)
 *   TTL (4 bytes)
 *   RDLENGTH (2 bytes)
 *   RDATA (variable)
 */

static void build_template(struct dns_template *t, uint16_t qtype,
			   const uint8_t *rdata, uint16_t rdlen)
{
	uint8_t *p = t->answer;

	t->qtype = qtype;

	/* NAME: compression pointer to offset 12 */
	*p++ = 0xC0;
	*p++ = 0x0C;

	/* TYPE */
	uint16_t type_n = htons(qtype);
	memcpy(p, &type_n, 2);
	p += 2;

	/* CLASS IN */
	uint16_t class_n = htons(DNS_CLASS_IN);
	memcpy(p, &class_n, 2);
	p += 2;

	/* TTL */
	uint32_t ttl_n = htonl(DNS_TTL);
	memcpy(p, &ttl_n, 4);
	p += 4;

	/* RDLENGTH */
	uint16_t rdlen_n = htons(rdlen);
	memcpy(p, &rdlen_n, 2);
	p += 2;

	/* RDATA */
	memcpy(p, rdata, rdlen);
	p += rdlen;

	t->answer_len = (uint16_t)(p - t->answer);
}

void dns_templates_init(void)
{
	/* A record: 127.0.0.1 */
	{
		uint8_t rdata[4] = { 127, 0, 0, 1 };
		build_template(&dns_templates[0], DNS_TYPE_A, rdata, 4);
	}

	/* AAAA record: ::1 */
	{
		uint8_t rdata[16] = { 0, 0, 0, 0, 0, 0, 0, 0,
				      0, 0, 0, 0, 0, 0, 0, 1 };
		build_template(&dns_templates[1], DNS_TYPE_AAAA, rdata, 16);
	}

	/* CNAME record: test.local. */
	{
		/* Wire format: \x04test\x05local\x00 */
		uint8_t rdata[] = { 4, 't', 'e', 's', 't',
				    5, 'l', 'o', 'c', 'a', 'l', 0 };
		build_template(&dns_templates[2], DNS_TYPE_CNAME, rdata, sizeof(rdata));
	}

	/* MX record: priority 10, mail.local. */
	{
		/* Wire format: \x00\x0A (priority 10) + \x04mail\x05local\x00 */
		uint8_t rdata[] = { 0, 10,
				    4, 'm', 'a', 'i', 'l',
				    5, 'l', 'o', 'c', 'a', 'l', 0 };
		build_template(&dns_templates[3], DNS_TYPE_MX, rdata, sizeof(rdata));
	}

	/* HTTPS/SVCB record (type 65): SvcPriority=1, TargetName="." (root), no SvcParams */
	{
		/* Wire format: \x00\x01 (priority 1) + \x00 (root label = ".") */
		uint8_t rdata[] = { 0, 1, 0 };
		build_template(&dns_templates[4], DNS_TYPE_HTTPS, rdata, sizeof(rdata));
	}
}

/*
 * Find the template for a given QTYPE.
 * Returns NULL if not found (will respond with NOTIMP).
 */
static const struct dns_template *find_template(uint16_t qtype)
{
	for (int i = 0; i < DNS_NUM_TEMPLATES; i++) {
		if (dns_templates[i].qtype == qtype)
			return &dns_templates[i];
	}
	return NULL;
}

/*
 * Scan QNAME in DNS question section to find its length.
 * Returns length including the terminating zero byte,
 * or 0 if malformed.
 */
static uint16_t scan_qname(const uint8_t *qname, const uint8_t *pkt_end)
{
	const uint8_t *p = qname;

	while (p < pkt_end) {
		uint8_t label_len = *p;
		if (label_len == 0) {
			p++; /* consume the zero byte */
			return (uint16_t)(p - qname);
		}
		/* Compression pointers not expected in questions */
		if (label_len >= 0xC0)
			return 0;
		/* Label too long */
		if (label_len > 63)
			return 0;
		p += 1 + label_len;
	}

	return 0; /* ran off end of packet */
}

uint32_t process_nxdomain_packet(uint8_t *pkt, uint32_t len)
{
	const uint32_t min_hdr = sizeof(struct eth_hdr) + sizeof(struct ipv4_hdr)
				 + sizeof(struct udp_hdr) + sizeof(struct dns_hdr);

	if (__builtin_expect(len < min_hdr, 0))
		return 0;

	struct eth_hdr *eth = (struct eth_hdr *)pkt;
	struct ipv4_hdr *ip = (struct ipv4_hdr *)(pkt + sizeof(struct eth_hdr));
	struct udp_hdr *udp = (struct udp_hdr *)(pkt + sizeof(struct eth_hdr)
				+ ((ip->ihl_ver & 0x0F) * 4));
	struct dns_hdr *dns = (struct dns_hdr *)((uint8_t *)udp + sizeof(struct udp_hdr));

	/* Swap L2/L3/L4 headers */
	eth_swap(eth);
	ipv4_swap(ip);
	udp_swap(udp);

	/* Set NXDOMAIN response: keep ID and question, no answer sections */
	dns->flags = htons(DNS_FLAGS_NXDOMAIN);
	dns->qdcount = htons(1);
	dns->ancount = 0;
	dns->nscount = 0;
	dns->arcount = 0;

	/* Recompute IP checksum (src/dst swapped) */
	ip_checksum_recompute(ip);
	udp->check = 0;

	return len;
}

uint32_t process_dns_packet(uint8_t *pkt, uint32_t len, uint16_t *qtype_out)
{
	const uint32_t min_hdr = sizeof(struct eth_hdr) + sizeof(struct ipv4_hdr)
				 + sizeof(struct udp_hdr) + sizeof(struct dns_hdr);

	if (__builtin_expect(len < min_hdr, 0))
		return 0;

	struct eth_hdr *eth = (struct eth_hdr *)pkt;
	struct ipv4_hdr *ip = (struct ipv4_hdr *)(pkt + sizeof(struct eth_hdr));
	uint32_t ip_hdr_len = (ip->ihl_ver & 0x0F) * 4;

	if (__builtin_expect(ip_hdr_len < 20, 0))
		return 0;

	struct udp_hdr *udp = (struct udp_hdr *)(pkt + sizeof(struct eth_hdr) + ip_hdr_len);
	struct dns_hdr *dns = (struct dns_hdr *)((uint8_t *)udp + sizeof(struct udp_hdr));

	/* Pointer to start of question section */
	uint8_t *question = (uint8_t *)(dns + 1);
	uint8_t *pkt_end = pkt + len;

	if (__builtin_expect((uint8_t *)question >= pkt_end, 0))
		return 0;

	/* Scan QNAME to find its length */
	uint16_t qname_len = scan_qname(question, pkt_end);
	if (__builtin_expect(qname_len == 0, 0))
		return 0;

	/* QTYPE and QCLASS follow QNAME (4 bytes) */
	uint8_t *qtype_ptr = question + qname_len;
	if (__builtin_expect(qtype_ptr + 4 > pkt_end, 0))
		return 0;

	uint16_t qtype;
	memcpy(&qtype, qtype_ptr, 2);
	qtype = ntohs(qtype);

	if (qtype_out)
		*qtype_out = qtype;

	/* Total question section length: QNAME + QTYPE(2) + QCLASS(2) */
	uint16_t question_len = qname_len + 4;

	/* Find matching template */
	const struct dns_template *tmpl = find_template(qtype);

	/* Swap L2/L3/L4 headers */
	eth_swap(eth);
	ipv4_swap(ip);
	udp_swap(udp);

	/* Build DNS response header */
	/* Keep dns->id (transaction ID) */
	if (__builtin_expect(tmpl != NULL, 1)) {
		dns->flags = htons(DNS_FLAGS_RESPONSE);
		dns->qdcount = htons(1);
		dns->ancount = htons(1);
		dns->nscount = 0;
		dns->arcount = 0;

		/* Append answer section after question */
		uint8_t *answer_dst = question + question_len;
		memcpy(answer_dst, tmpl->answer, tmpl->answer_len);

		/* Compute new lengths */
		uint16_t dns_len = sizeof(struct dns_hdr) + question_len + tmpl->answer_len;
		uint16_t udp_len = sizeof(struct udp_hdr) + dns_len;
		uint16_t ip_total = ip_hdr_len + udp_len;

		udp->len = htons(udp_len);
		udp->check = 0; /* Valid for UDP/IPv4 per RFC 768 */

		ip->tot_len = htons(ip_total);
		ip_checksum_recompute(ip);

		return sizeof(struct eth_hdr) + ip_total;
	} else {
		/* Unknown QTYPE: respond with NOTIMP */
		dns->flags = htons(DNS_FLAGS_NOTIMP);
		dns->qdcount = htons(1);
		dns->ancount = 0;
		dns->nscount = 0;
		dns->arcount = 0;

		uint16_t dns_len = sizeof(struct dns_hdr) + question_len;
		uint16_t udp_len = sizeof(struct udp_hdr) + dns_len;
		uint16_t ip_total = ip_hdr_len + udp_len;

		udp->len = htons(udp_len);
		udp->check = 0;

		ip->tot_len = htons(ip_total);
		ip_checksum_recompute(ip);

		return sizeof(struct eth_hdr) + ip_total;
	}
}
