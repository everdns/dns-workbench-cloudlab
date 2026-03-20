#ifndef STATS_H
#define STATS_H

#include <stdint.h>
#include <stdio.h>

/* Per-thread statistics, cache-line aligned to avoid false sharing */
struct thread_stats {
	uint64_t rx_packets;
	uint64_t tx_packets;
	uint64_t rx_bytes;
	uint64_t tx_bytes;
	uint64_t rx_drops;
	uint64_t parse_errors;
	uint64_t type_a;
	uint64_t type_aaaa;
	uint64_t type_cname;
	uint64_t type_mx;
	uint64_t type_https;
	uint64_t type_other;
	uint64_t _pad[4]; /* pad to 128 bytes (2 cache lines) */
} __attribute__((aligned(64)));

/* Aggregated statistics */
struct agg_stats {
	uint64_t rx_packets;
	uint64_t tx_packets;
	uint64_t rx_bytes;
	uint64_t tx_bytes;
	uint64_t rx_drops;
	uint64_t parse_errors;
	uint64_t type_a;
	uint64_t type_aaaa;
	uint64_t type_cname;
	uint64_t type_mx;
	uint64_t type_https;
	uint64_t type_other;
};

/*
 * Aggregate per-thread stats into a single summary.
 * thread_stats: array of per-thread stats
 * num_threads:  number of threads
 * out:          output aggregated stats
 */
void stats_aggregate(const struct thread_stats *thread_stats,
		     int num_threads, struct agg_stats *out);

/*
 * Print end-of-run summary to the given stream.
 * duration_secs: total run time in seconds (floating point)
 * verbose: if non-zero, also print per-thread breakdown
 */
void stats_print(FILE *stream, const struct agg_stats *agg,
		 double duration_secs);

/*
 * Print per-thread breakdown to the given stream.
 */
void stats_print_per_thread(FILE *stream, const struct thread_stats *stats,
			    int num_threads);

#endif /* STATS_H */
