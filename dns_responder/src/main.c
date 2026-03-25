#define _GNU_SOURCE

#include "af_xdp.h"
#include "dns.h"
#include "stats.h"

#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <errno.h>
#include <getopt.h>
#include <locale.h>
#include <linux/if_link.h>
#include <net/if.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_QUEUES 64

static volatile sig_atomic_t running = 1;

static void signal_handler(int sig)
{
	(void)sig;
	running = 0;
}

struct config {
	char ifname[IF_NAMESIZE];
	int  num_queues;
	int  duration;
	int  batch_size;
	int  frame_count;
	int  zerocopy;
	int  verbose;
	char xdp_prog_path[512];
	char output_path[512];
	char timestamps_path[512];
	int  ts_range;            /* track min/max timestamps for QPS */
};

static void config_defaults(struct config *cfg)
{
	memset(cfg, 0, sizeof(*cfg));
	cfg->num_queues = 0;  /* auto-detect */
	cfg->duration = 0;    /* run until signal */
	cfg->batch_size = DEFAULT_BATCH_SIZE;
	cfg->frame_count = DEFAULT_FRAME_COUNT;
	cfg->zerocopy = 1;    /* try zerocopy, fallback to copy */
	cfg->verbose = 0;
	snprintf(cfg->xdp_prog_path, sizeof(cfg->xdp_prog_path),
		 "./xdp/xdp_dns_redirect.o");
}

static void usage(const char *prog)
{
	fprintf(stderr,
		"Usage: %s [OPTIONS]\n"
		"\n"
		"High-performance AF_XDP DNS response generator\n"
		"\n"
		"Required:\n"
		"  -i, --interface IFACE    Network interface\n"
		"\n"
		"Optional:\n"
		"  -q, --queues N           Number of RX queues/threads (default: auto-detect)\n"
		"  -d, --duration SECS      Run duration in seconds (0 = until SIGINT)\n"
		"  -o, --output FILE        Write stats to file\n"
		"  -z, --zerocopy           Force zero-copy mode (fail if unsupported)\n"
		"  -Z, --no-zerocopy        Disable zero-copy, use copy mode\n"
		"  -b, --batch-size N       RX/TX batch size (default: %d)\n"
		"  -f, --frame-count N      UMEM frames per queue (default: %d)\n"
		"  -t, --timestamps FILE    Write per-packet RX timestamps to file\n"
	"  -T, --ts-range           Track min/max RX timestamps for actual QPS\n"
		"  -x, --xdp-prog FILE      Path to XDP object file\n"
		"  -v, --verbose            Print per-thread stats\n"
		"  -h, --help               Show this help\n",
		prog, DEFAULT_BATCH_SIZE, DEFAULT_FRAME_COUNT);
}

static int parse_args(int argc, char **argv, struct config *cfg)
{
	static struct option long_opts[] = {
		{ "interface",   required_argument, NULL, 'i' },
		{ "queues",      required_argument, NULL, 'q' },
		{ "duration",    required_argument, NULL, 'd' },
		{ "output",      required_argument, NULL, 'o' },
		{ "timestamps",  required_argument, NULL, 't' },
		{ "ts-range",    no_argument,       NULL, 'T' },
		{ "zerocopy",    no_argument,       NULL, 'z' },
		{ "no-zerocopy", no_argument,       NULL, 'Z' },
		{ "batch-size",  required_argument, NULL, 'b' },
		{ "frame-count", required_argument, NULL, 'f' },
		{ "xdp-prog",   required_argument, NULL, 'x' },
		{ "verbose",     no_argument,       NULL, 'v' },
		{ "help",        no_argument,       NULL, 'h' },
		{ NULL, 0, NULL, 0 },
	};

	int opt;
	while ((opt = getopt_long(argc, argv, "i:q:d:o:t:TzZb:f:x:vh",
				  long_opts, NULL)) != -1) {
		switch (opt) {
		case 'i':
			snprintf(cfg->ifname, sizeof(cfg->ifname), "%s", optarg);
			break;
		case 'q':
			cfg->num_queues = atoi(optarg);
			break;
		case 'd':
			cfg->duration = atoi(optarg);
			break;
		case 'o':
			snprintf(cfg->output_path, sizeof(cfg->output_path),
				 "%s", optarg);
			break;
		case 't':
			snprintf(cfg->timestamps_path,
				 sizeof(cfg->timestamps_path), "%s", optarg);
			break;
		case 'T':
			cfg->ts_range = 1;
			break;
		case 'z':
			cfg->zerocopy = 2; /* force */
			break;
		case 'Z':
			cfg->zerocopy = 0;
			break;
		case 'b':
			cfg->batch_size = atoi(optarg);
			break;
		case 'f':
			cfg->frame_count = atoi(optarg);
			break;
		case 'x':
			snprintf(cfg->xdp_prog_path,
				 sizeof(cfg->xdp_prog_path), "%s", optarg);
			break;
		case 'v':
			cfg->verbose = 1;
			break;
		case 'h':
			usage(argv[0]);
			exit(0);
		default:
			usage(argv[0]);
			return -1;
		}
	}

	if (cfg->ifname[0] == '\0') {
		fprintf(stderr, "ERROR: --interface is required\n");
		usage(argv[0]);
		return -1;
	}

	return 0;
}

/*
 * Auto-detect the number of RX queues on an interface.
 */
static int detect_queue_count(const char *ifname)
{
	char path[256];
	int count = 0;

	for (int i = 0; i < MAX_QUEUES; i++) {
		snprintf(path, sizeof(path),
			 "/sys/class/net/%s/queues/rx-%d", ifname, i);
		if (access(path, F_OK) != 0)
			break;
		count++;
	}

	return count > 0 ? count : 1;
}

/*
 * Load the XDP program and return the BPF object + XSKMAP fd.
 * Attaches the XDP program to the interface.
 */
static int load_xdp_program(const char *path, const char *ifname,
			    struct bpf_object **obj_out, int *xsks_map_fd,
			    uint32_t *xdp_flags_used)
{
	struct bpf_object *obj;
	struct bpf_program *prog;
	struct bpf_map *map;
	int prog_fd;
	int ifindex;
	int ret;

	ifindex = if_nametoindex(ifname);
	if (ifindex == 0) {
		fprintf(stderr, "ERROR: interface %s not found\n", ifname);
		return -1;
	}

	obj = bpf_object__open_file(path, NULL);
	if (libbpf_get_error(obj)) {
		fprintf(stderr, "ERROR: failed to open XDP object %s: %s\n",
			path, strerror(errno));
		return -1;
	}

	ret = bpf_object__load(obj);
	if (ret) {
		fprintf(stderr, "ERROR: failed to load XDP object: %s\n",
			strerror(-ret));
		bpf_object__close(obj);
		return -1;
	}

	prog = bpf_object__find_program_by_name(obj, "xdp_dns_redirect");
	if (!prog) {
		fprintf(stderr, "ERROR: XDP program 'xdp_dns_redirect' not found\n");
		bpf_object__close(obj);
		return -1;
	}

	prog_fd = bpf_program__fd(prog);

	map = bpf_object__find_map_by_name(obj, "xsks_map");
	if (!map) {
		fprintf(stderr, "ERROR: XSKMAP 'xsks_map' not found\n");
		bpf_object__close(obj);
		return -1;
	}

	*xsks_map_fd = bpf_map__fd(map);

	/* Attach XDP program in DRV mode (native), fallback to SKB */
	ret = bpf_set_link_xdp_fd(ifindex, prog_fd, XDP_FLAGS_DRV_MODE);
	if (ret) {
		fprintf(stderr, "WARNING: native XDP attach failed, trying SKB mode\n");
		ret = bpf_set_link_xdp_fd(ifindex, prog_fd, XDP_FLAGS_SKB_MODE);
		if (ret) {
			fprintf(stderr, "ERROR: XDP attach failed: %s\n",
				strerror(-ret));
			bpf_object__close(obj);
			return -1;
		}
		*xdp_flags_used = XDP_FLAGS_SKB_MODE;
	} else {
		*xdp_flags_used = XDP_FLAGS_DRV_MODE;
	}

	*obj_out = obj;
	return 0;
}

static void detach_xdp(const char *ifname)
{
	int ifindex = if_nametoindex(ifname);
	if (ifindex == 0)
		return;

	/* Try detaching from both modes */
	bpf_set_link_xdp_fd(ifindex, -1, XDP_FLAGS_DRV_MODE);
	bpf_set_link_xdp_fd(ifindex, -1, XDP_FLAGS_SKB_MODE);
}

static double timespec_diff(struct timespec *start, struct timespec *end)
{
	return (double)(end->tv_sec - start->tv_sec)
	       + (double)(end->tv_nsec - start->tv_nsec) / 1e9;
}

int main(int argc, char **argv)
{
	struct config cfg;
	struct bpf_object *bpf_obj = NULL;
	struct worker_ctx *workers = NULL;
	pthread_t *threads = NULL;
	int xsks_map_fd = -1;
	int ret = 1;

	setlocale(LC_NUMERIC, "");
	config_defaults(&cfg);

	if (parse_args(argc, argv, &cfg) < 0)
		return 1;

	/* Auto-detect queues if not specified */
	if (cfg.num_queues == 0)
		cfg.num_queues = detect_queue_count(cfg.ifname);

	if (cfg.num_queues > MAX_QUEUES) {
		fprintf(stderr, "ERROR: too many queues (%d > %d)\n",
			cfg.num_queues, MAX_QUEUES);
		return 1;
	}

	fprintf(stderr, "DNS Responder starting:\n");
	fprintf(stderr, "  Interface:  %s\n", cfg.ifname);
	fprintf(stderr, "  Queues:     %d\n", cfg.num_queues);
	fprintf(stderr, "  Batch size: %d\n", cfg.batch_size);
	fprintf(stderr, "  Frames:     %d per queue\n", cfg.frame_count);
	fprintf(stderr, "  Zero-copy:  %s\n",
		cfg.zerocopy == 2 ? "forced" :
		cfg.zerocopy == 1 ? "preferred" : "disabled");
	if (cfg.duration > 0)
		fprintf(stderr, "  Duration:   %d seconds\n", cfg.duration);
	else
		fprintf(stderr, "  Duration:   until SIGINT/SIGTERM\n");
	fprintf(stderr, "\n");

	/* Initialize DNS response templates */
	dns_templates_init();

	/* Set up signal handlers */
	struct sigaction sa;
	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = signal_handler;
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);

	/* Load and attach XDP program */
	uint32_t xdp_flags = 0;
	fprintf(stderr, "Loading XDP program from %s...\n", cfg.xdp_prog_path);
	if (load_xdp_program(cfg.xdp_prog_path, cfg.ifname,
			     &bpf_obj, &xsks_map_fd, &xdp_flags) < 0)
		goto cleanup;

	fprintf(stderr, "XDP program attached to %s (%s mode)\n", cfg.ifname,
		xdp_flags == XDP_FLAGS_DRV_MODE ? "native" : "SKB");

	/* Allocate worker contexts and thread handles */
	workers = calloc(cfg.num_queues, sizeof(struct worker_ctx));
	threads = calloc(cfg.num_queues, sizeof(pthread_t));
	if (!workers || !threads) {
		fprintf(stderr, "ERROR: memory allocation failed\n");
		goto cleanup;
	}

	/* Determine bind flags — zero-copy requires native (DRV) mode */
	uint16_t bind_flags = 0;
	if (xdp_flags == XDP_FLAGS_SKB_MODE) {
		if (cfg.zerocopy == 2) {
			fprintf(stderr,
				"ERROR: zero-copy forced but XDP is in SKB mode "
				"(not supported)\n");
			goto cleanup;
		}
		bind_flags = XDP_COPY;
	} else if (cfg.zerocopy >= 1) {
		bind_flags = XDP_ZEROCOPY;
	} else {
		bind_flags = XDP_COPY;
	}

	/* Initialize per-queue UMEM and AF_XDP sockets */
	int record_ts = 0;
	if (cfg.timestamps_path[0] != '\0')
		record_ts = 1;
	else if (cfg.ts_range)
		record_ts = 2;
	for (int q = 0; q < cfg.num_queues; q++) {
		struct worker_ctx *w = &workers[q];
		w->running = (volatile int *)&running;
		w->batch_size = cfg.batch_size;
		w->cpu_id = q;
		w->record_timestamps = record_ts;

		if (xsk_umem_init(&w->xsk, cfg.frame_count,
				  DEFAULT_FRAME_SIZE) < 0)
			goto cleanup;

		if (xsk_socket_init(&w->xsk, cfg.ifname, q,
				    xdp_flags, bind_flags) < 0) {
			/* Fallback to copy mode if zerocopy=preferred */
			if (cfg.zerocopy == 1 && bind_flags == XDP_ZEROCOPY) {
				fprintf(stderr,
					"WARNING: zero-copy failed for queue %d, "
					"falling back to copy mode\n", q);
				bind_flags = XDP_COPY;
				if (xsk_socket_init(&w->xsk, cfg.ifname, q,
						    xdp_flags, bind_flags) < 0)
					goto cleanup;
			} else {
				goto cleanup;
			}
		}

		/* Register AF_XDP socket in XSKMAP */
		int xsk_fd = xsk_socket__fd(w->xsk.xsk);
		if (bpf_map_update_elem(xsks_map_fd, &q, &xsk_fd, 0) < 0) {
			fprintf(stderr,
				"ERROR: failed to update xsks_map for queue %d: %s\n",
				q, strerror(errno));
			goto cleanup;
		}

		/* Populate fill ring with initial frames */
		xsk_populate_fill_ring(&w->xsk);

		fprintf(stderr, "  Queue %d: AF_XDP socket ready (fd=%d)\n",
			q, xsk_fd);
	}

	fprintf(stderr, "\nStarting %d worker threads...\n", cfg.num_queues);

	struct timespec start_time;
	clock_gettime(CLOCK_MONOTONIC, &start_time);

	/* Pass start time to workers for timestamp recording */
	for (int q = 0; q < cfg.num_queues; q++)
		workers[q].start_time = start_time;

	/* Launch worker threads */
	for (int q = 0; q < cfg.num_queues; q++) {
		if (pthread_create(&threads[q], NULL, worker_thread,
				   &workers[q]) != 0) {
			fprintf(stderr, "ERROR: pthread_create queue %d: %s\n",
				q, strerror(errno));
			running = 0;
			goto join;
		}

		/* Pin thread to CPU */
		cpu_set_t cpuset;
		CPU_ZERO(&cpuset);
		CPU_SET(workers[q].cpu_id, &cpuset);
		pthread_setaffinity_np(threads[q], sizeof(cpuset), &cpuset);
	}

	fprintf(stderr, "Listening for DNS queries on %s... (Ctrl+C to stop)\n\n",
		cfg.ifname);

	/* Wait for duration or signal */
	if (cfg.duration > 0) {
		struct timespec remaining = {
			.tv_sec = cfg.duration,
			.tv_nsec = 0,
		};
		while (running && remaining.tv_sec > 0) {
			struct timespec one_sec = { .tv_sec = 1, .tv_nsec = 0 };
			nanosleep(&one_sec, NULL);
			remaining.tv_sec--;
		}
		running = 0;
	} else {
		while (running)
			pause();
	}

join:
	/* Join worker threads */
	for (int q = 0; q < cfg.num_queues; q++) {
		if (threads[q])
			pthread_join(threads[q], NULL);
	}

	struct timespec end_time;
	clock_gettime(CLOCK_MONOTONIC, &end_time);
	double duration = timespec_diff(&start_time, &end_time);

	/* Aggregate and print stats */
	struct thread_stats *all_stats = malloc(cfg.num_queues * sizeof(struct thread_stats));
	if (all_stats) {
		for (int q = 0; q < cfg.num_queues; q++) {
			all_stats[q] = workers[q].stats;
			if (record_ts == 2) {
				all_stats[q].ts_min_ns = workers[q].ts_min_ns;
				all_stats[q].ts_max_ns = workers[q].ts_max_ns;
			} else if (record_ts == 1 && workers[q].ts.count > 0) {
				all_stats[q].ts_min_ns = workers[q].ts.data[0];
				all_stats[q].ts_max_ns = workers[q].ts.data[workers[q].ts.count - 1];
			}
		}

		struct agg_stats agg;
		stats_aggregate(all_stats, cfg.num_queues, &agg);

		stats_print(stderr, &agg, duration);

		if (cfg.verbose)
			stats_print_per_thread(stderr, all_stats, cfg.num_queues);

		/* Write to output file if specified */
		if (cfg.output_path[0] != '\0') {
			FILE *f = fopen(cfg.output_path, "w");
			if (f) {
				stats_print(f, &agg, duration);
				if (cfg.verbose)
					stats_print_per_thread(f, all_stats,
							       cfg.num_queues);
				fclose(f);
				fprintf(stderr, "Stats written to %s\n",
					cfg.output_path);
			} else {
				fprintf(stderr, "WARNING: could not open %s: %s\n",
					cfg.output_path, strerror(errno));
			}
		}

		free(all_stats);
	}

	/* Write per-packet timestamps if requested */
	if (cfg.timestamps_path[0] != '\0') {
		FILE *tf = fopen(cfg.timestamps_path, "w");
		if (tf) {
			fprintf(tf, "# Per-packet RX timestamps (nanoseconds "
				"since start)\n");
			fprintf(tf, "# Merge-sorted across %d worker threads\n",
				cfg.num_queues);

			/* Count total timestamps */
			uint64_t total_ts = 0;
			for (int q = 0; q < cfg.num_queues; q++)
				total_ts += workers[q].ts.count;

			/* Merge-sort across threads using per-thread cursors */
			uint64_t *cursors = calloc(cfg.num_queues,
						   sizeof(uint64_t));
			if (cursors) {
				for (uint64_t written = 0; written < total_ts;
				     written++) {
					int best = -1;
					uint64_t best_ts = UINT64_MAX;
					for (int q = 0; q < cfg.num_queues;
					     q++) {
						if (cursors[q] >=
						    workers[q].ts.count)
							continue;
						uint64_t ts =
							workers[q].ts
								.data[cursors[q]];
						if (ts < best_ts) {
							best_ts = ts;
							best = q;
						}
					}
					if (best < 0)
						break;
					fprintf(tf, "%lu\n", best_ts);
					cursors[best]++;
				}
				free(cursors);
			}

			fclose(tf);
			fprintf(stderr, "Timestamps written to %s (%lu entries)\n",
				cfg.timestamps_path, total_ts);
		} else {
			fprintf(stderr, "WARNING: could not open %s: %s\n",
				cfg.timestamps_path, strerror(errno));
		}
	}

	/* Free timestamp buffers */
	if (workers) {
		for (int q = 0; q < cfg.num_queues; q++)
			free(workers[q].ts.data);
	}

	ret = 0;

cleanup:
	/* Cleanup AF_XDP sockets */
	if (workers) {
		for (int q = 0; q < cfg.num_queues; q++)
			xsk_cleanup(&workers[q].xsk);
	}

	/* Detach XDP program */
	detach_xdp(cfg.ifname);
	fprintf(stderr, "XDP program detached from %s\n", cfg.ifname);

	if (bpf_obj)
		bpf_object__close(bpf_obj);

	free(workers);
	free(threads);

	return ret;
}
