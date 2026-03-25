#include "stats.h"

#include <stdint.h>
#include <string.h>

void stats_aggregate(const struct thread_stats *thread_stats,
		     int num_threads, struct agg_stats *out)
{
	memset(out, 0, sizeof(*out));

	uint64_t global_min = UINT64_MAX;
	uint64_t global_max = 0;

	for (int i = 0; i < num_threads; i++) {
		const struct thread_stats *t = &thread_stats[i];
		out->rx_packets   += t->rx_packets;
		out->tx_packets   += t->tx_packets;
		out->rx_bytes     += t->rx_bytes;
		out->tx_bytes     += t->tx_bytes;
		out->rx_drops     += t->rx_drops;
		out->parse_errors += t->parse_errors;
		out->type_a       += t->type_a;
		out->type_aaaa    += t->type_aaaa;
		out->type_cname   += t->type_cname;
		out->type_mx      += t->type_mx;
		out->type_https   += t->type_https;
		out->type_other   += t->type_other;

		if (t->ts_min_ns < global_min)
			global_min = t->ts_min_ns;
		if (t->ts_max_ns > global_max)
			global_max = t->ts_max_ns;
	}

	if (global_min < global_max)
		out->actual_duration_secs = (double)(global_max - global_min) / 1e9;
}

static void print_count(FILE *stream, const char *label, uint64_t count)
{
	if (count >= 1000000)
		fprintf(stream, "  %-16s %'lu (%.2fM)\n", label, count,
			(double)count / 1e6);
	else
		fprintf(stream, "  %-16s %'lu\n", label, count);
}

void stats_print(FILE *stream, const struct agg_stats *agg,
		 double duration_secs)
{
	fprintf(stream, "\n--- Run complete (%.1fs) ---\n", duration_secs);
	fprintf(stream, "Packets:\n");
	print_count(stream, "RX total:", agg->rx_packets);
	print_count(stream, "TX total:", agg->tx_packets);
	print_count(stream, "Parse errors:", agg->parse_errors);
	print_count(stream, "Drops:", agg->rx_drops);

	fprintf(stream, "\nThroughput:\n");
	if (duration_secs > 0) {
		fprintf(stream, "  %-16s %.0f pps (%.2f Mpps)\n",
			"Avg RX:",
			(double)agg->rx_packets / duration_secs,
			(double)agg->rx_packets / duration_secs / 1e6);
		fprintf(stream, "  %-16s %.0f pps (%.2f Mpps)\n",
			"Avg TX:",
			(double)agg->tx_packets / duration_secs,
			(double)agg->tx_packets / duration_secs / 1e6);
		fprintf(stream, "  %-16s %.2f Gbps\n",
			"RX bandwidth:",
			(double)agg->rx_bytes * 8 / duration_secs / 1e9);
		fprintf(stream, "  %-16s %.2f Gbps\n",
			"TX bandwidth:",
			(double)agg->tx_bytes * 8 / duration_secs / 1e9);
	}

	if (agg->actual_duration_secs > 0) {
		fprintf(stream, "\nActual traffic window: %.3fs "
			"(first pkt to last pkt)\n",
			agg->actual_duration_secs);
		fprintf(stream, "  %-16s %.0f qps (%.2f Mqps)\n",
			"RX QPS:",
			(double)agg->rx_packets / agg->actual_duration_secs,
			(double)agg->rx_packets / agg->actual_duration_secs / 1e6);
		fprintf(stream, "  %-16s %.0f qps (%.2f Mqps)\n",
			"TX QPS:",
			(double)agg->tx_packets / agg->actual_duration_secs,
			(double)agg->tx_packets / agg->actual_duration_secs / 1e6);
	}

	fprintf(stream, "\nQuery types:\n");
	print_count(stream, "A:", agg->type_a);
	print_count(stream, "AAAA:", agg->type_aaaa);
	print_count(stream, "CNAME:", agg->type_cname);
	print_count(stream, "MX:", agg->type_mx);
	print_count(stream, "HTTPS:", agg->type_https);
	print_count(stream, "Other/NOTIMP:", agg->type_other);

	fprintf(stream, "\n");
}

void stats_print_per_thread(FILE *stream, const struct thread_stats *stats,
			    int num_threads)
{
	fprintf(stream, "\nPer-thread breakdown:\n");
	fprintf(stream, "  %-8s %12s %12s %12s\n",
		"Thread", "RX pkts", "TX pkts", "Errors");
	fprintf(stream, "  %-8s %12s %12s %12s\n",
		"------", "-------", "-------", "------");

	for (int i = 0; i < num_threads; i++) {
		const struct thread_stats *t = &stats[i];
		fprintf(stream, "  %-8d %12lu %12lu %12lu\n",
			i, t->rx_packets, t->tx_packets, t->parse_errors);
	}
}
