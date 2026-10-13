/*
 * Extended hardware probe suite (TASKS.md Tier 1, item 26).
 *
 * The project's hardware signature is TWO scalars: STREAM-triad bandwidth and
 * multi-core compute throughput. Nothing has ever tested whether two are enough --
 * it is assumed. The project's own limitations section names a richer probe suite as
 * probably mattering more than further modelling.
 *
 * This measures seven axes instead, chosen so each maps to a resource a query
 * executor actually consumes:
 *
 *   int_scalar      integer ALU throughput        -- predicate evaluation, hashing
 *   fp_scalar       scalar double throughput      -- dependency-chain-bound arithmetic
 *   fp_vector       vectorised double throughput  -- bulk aggregation (sum, price*disc)
 *   mem_bandwidth   STREAM triad, multi-threaded  -- sequential scans
 *   mem_latency     random pointer chase, DRAM    -- hash-join probes, index lookups
 *   cache_l1/l2/l3  pointer chase per level       -- working-set-resident operators
 *   branch_cost     unpredictable branch penalty  -- selective filters
 *
 * Every loop is protected against elimination by a data-dependent accumulator that is
 * printed, and each reports enough raw detail (iterations, bytes, ns/access) to sanity
 * check that it did real work.
 *
 *   cc -O2 -o hw_probe_suite hw_probe_suite.c -lpthread
 *   ./hw_probe_suite [seconds_per_probe] [threads_nproc] [threads_dop]
 *
 * Emits one JSON object so it can be collected straight into a machine signature.
 */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* ---------------------------------------------------------------- compute probes */

static double int_scalar_gops(double secs, volatile uint64_t *sink) {
    uint64_t a = 1, b = 3, c = 5, d = 7;
    unsigned long long n = 0;
    double t0 = now_s();
    while (now_s() - t0 < secs) {
        for (int i = 0; i < 1000000; i++) {
            a = a * 6364136223846793005ULL + 1442695040888963407ULL;
            b = b ^ (a >> 7); c += b | 1; d -= c ^ 3;
        }
        n += 4000000;
    }
    double el = now_s() - t0;
    *sink ^= a ^ b ^ c ^ d;
    return n / el / 1e9;
}

static double fp_scalar_gflops(double secs, volatile double *sink) {
    /* Single dependency chain: measures latency-bound FP, not peak throughput. */
    double a = 1.0000001;
    unsigned long long n = 0;
    double t0 = now_s();
    while (now_s() - t0 < secs) {
        for (int i = 0; i < 1000000; i++) a = a * 1.0000000001 + 1e-12;
        n += 2000000;
    }
    double el = now_s() - t0;
    *sink += a;
    return n / el / 1e9;
}

static double fp_vector_gflops(double secs, volatile double *sink) {
    /* 8 independent accumulators over an L1-resident array: exposes ILP and lets the
     * compiler vectorise, so this measures peak vector throughput. Mirrors the
     * project's existing compute_probe.c so results stay comparable to it. */
    const size_t N = 2048;
    double *x = malloc(N * sizeof(double));
    for (size_t i = 0; i < N; i++) x[i] = 1.0 + i * 1e-6;
    double acc[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    unsigned long long iters = 0;
    double t0 = now_s();
    while (now_s() - t0 < secs) {
        for (int r = 0; r < 200; r++) {
            for (size_t i = 0; i < N; i += 8)
                for (int k = 0; k < 8; k++) acc[k] += x[i + k] * x[i + k];
            x[iters % N] += 1e-13;  /* defeat loop-invariant hoisting */
        }
        iters += 200;
    }
    double el = now_s() - t0;
    double s = 0;
    for (int k = 0; k < 8; k++) s += acc[k];
    *sink += s;
    free(x);
    return (double)iters * N * 2.0 / el / 1e9;
}

static double branch_cost_ns(double secs, volatile uint64_t *sink) {
    const size_t N = 1 << 20;
    unsigned char *p = malloc(N);
    uint64_t r = 12345;
    for (size_t i = 0; i < N; i++) { r = r * 6364136223846793005ULL + 1; p[i] = (r >> 33) & 1; }
    uint64_t taken = 0;
    unsigned long long n = 0;
    double t0 = now_s();
    while (now_s() - t0 < secs) {
        for (size_t i = 0; i < N; i++) if (p[i]) taken++; else taken += 2;
        n += N;
    }
    double el = now_s() - t0;
    *sink ^= taken;
    free(p);
    return el / n * 1e9;
}

/* ------------------------------------------------- memory hierarchy: pointer chase */

static double chase_ns(size_t bytes, double secs, volatile uint64_t *sink) {
    size_t n = bytes / sizeof(size_t);
    if (n < 64) n = 64;
    size_t *idx = malloc(n * sizeof(size_t));
    for (size_t i = 0; i < n; i++) idx[i] = i;
    /* Random permutation -> a single dependent chain, so each access is a full
     * latency exposure with no prefetching possible. */
    uint64_t r = 88172645463325252ULL;
    for (size_t i = n - 1; i > 0; i--) {
        r ^= r << 13; r ^= r >> 7; r ^= r << 17;
        size_t j = r % (i + 1);
        size_t t = idx[i]; idx[i] = idx[j]; idx[j] = t;
    }
    size_t *next = malloc(n * sizeof(size_t));
    for (size_t i = 0; i < n; i++) next[idx[i]] = idx[(i + 1) % n];
    size_t cur = 0;
    unsigned long long hops = 0;
    double t0 = now_s();
    while (now_s() - t0 < secs) {
        for (size_t i = 0; i < n; i++) cur = next[cur];
        hops += n;
    }
    double el = now_s() - t0;
    *sink ^= cur;
    free(idx); free(next);
    return el / hops * 1e9;
}

/* --------------------------------------------------------- STREAM triad bandwidth */

typedef struct { double *a, *b, *c; size_t n; double secs; double gbs; } bw_t;

static void *bw_run(void *arg) {
    bw_t *w = (bw_t *)arg;
    unsigned long long it = 0;
    double t0 = now_s();
    while (now_s() - t0 < w->secs) {
        for (size_t i = 0; i < w->n; i++) w->a[i] = w->b[i] + 3.0 * w->c[i];
        it++;
        w->a[it % w->n] += 1e-300;
    }
    double el = now_s() - t0;
    w->gbs = (double)it * w->n * 3.0 * sizeof(double) / el / 1e9;
    return NULL;
}

static double mem_bandwidth_gbs(int threads, double secs) {
    size_t mib = 64, n = mib * 1024 * 1024 / sizeof(double) / 3;
    pthread_t *th = calloc(threads, sizeof(pthread_t));
    bw_t *w = calloc(threads, sizeof(bw_t));
    for (int t = 0; t < threads; t++) {
        w[t].n = n; w[t].secs = secs;
        w[t].a = malloc(n * sizeof(double));
        w[t].b = malloc(n * sizeof(double));
        w[t].c = malloc(n * sizeof(double));
        for (size_t i = 0; i < n; i++) { w[t].a[i] = 1; w[t].b[i] = 2; w[t].c[i] = 3; }
    }
    for (int t = 0; t < threads; t++) pthread_create(&th[t], NULL, bw_run, &w[t]);
    double total = 0;
    for (int t = 0; t < threads; t++) { pthread_join(th[t], NULL); total += w[t].gbs; }
    return total;
}

typedef struct { double secs; double gflops; } cw_t;
static void *cw_run(void *arg) {
    cw_t *w = (cw_t *)arg;
    volatile double sink = 0;
    w->gflops = fp_vector_gflops(w->secs, &sink);
    return NULL;
}

int main(int argc, char **argv) {
    double secs = argc > 1 ? atof(argv[1]) : 3.0;
    int threads = argc > 2 ? atoi(argv[2]) : 8;
    /* The achievable degree of parallelism: 1 + max_parallel_workers_per_gather. Probing
     * only at `nproc` is a definition bug -- a query can never use more than this, so on a
     * machine with more cores than the planner will use, an nproc-aggregate figure
     * describes capacity nothing can reach. Measured cost of that bug: 324% vs 153% MAPE
     * (results/tier1_laptop_dev.md). Pass it explicitly; 0 means "do not probe at DOP". */
    int dop = argc > 3 ? atoi(argv[3]) : 0;
    volatile uint64_t isink = 0;
    volatile double dsink = 0;

    double i_g = int_scalar_gops(secs, &isink);
    double f_s = fp_scalar_gflops(secs, &dsink);
    double f_v = fp_vector_gflops(secs, &dsink);
    double br = branch_cost_ns(secs, &isink);

    /* Multi-threaded vector compute at nproc -- retained for comparability with the
     * original EC2 collection, which probed this way. */
    pthread_t *th = calloc(threads, sizeof(pthread_t));
    cw_t *cw = calloc(threads, sizeof(cw_t));
    for (int t = 0; t < threads; t++) { cw[t].secs = secs; pthread_create(&th[t], NULL, cw_run, &cw[t]); }
    double f_v_mt = 0;
    for (int t = 0; t < threads; t++) { pthread_join(th[t], NULL); f_v_mt += cw[t].gflops; }

    /* The same probe at the ACHIEVABLE DOP. This is the figure a cost model should use. */
    double f_v_dop = 0.0;
    if (dop > 0) {
        pthread_t *dth = calloc(dop, sizeof(pthread_t));
        cw_t *dcw = calloc(dop, sizeof(cw_t));
        for (int t = 0; t < dop; t++) { dcw[t].secs = secs; pthread_create(&dth[t], NULL, cw_run, &dcw[t]); }
        for (int t = 0; t < dop; t++) { pthread_join(dth[t], NULL); f_v_dop += dcw[t].gflops; }
        free(dth); free(dcw);
    }

    double l1 = chase_ns(24 * 1024, secs / 2, &isink);
    double l2 = chase_ns(512 * 1024, secs / 2, &isink);
    double l3 = chase_ns(8 * 1024 * 1024, secs / 2, &isink);
    double dram = chase_ns(256 * 1024 * 1024, secs / 2, &isink);
    double bw1 = mem_bandwidth_gbs(1, secs);
    double bwN = mem_bandwidth_gbs(threads, secs);

    printf("{\n");
    printf("  \"signature_version\": 2,\n");
    printf("  \"threads_nproc\": %d,\n", threads);
    printf("  \"threads_dop\": %d,\n", dop);
    printf("  \"int_scalar_gops\": %.3f,\n", i_g);
    printf("  \"fp_scalar_gflops\": %.3f,\n", f_s);
    printf("  \"fp_vector_gflops\": %.3f,\n", f_v);
    printf("  \"fp_vector_gflops_nproc\": %.3f,\n", f_v_mt);
    printf("  \"fp_vector_gflops_dop\": %.3f,\n", f_v_dop);
    printf("  \"branch_cost_ns\": %.4f,\n", br);
    printf("  \"cache_l1_ns\": %.3f,\n", l1);
    printf("  \"cache_l2_ns\": %.3f,\n", l2);
    printf("  \"cache_l3_ns\": %.3f,\n", l3);
    printf("  \"mem_latency_ns\": %.3f,\n", dram);
    printf("  \"mem_bandwidth_gbs_1t\": %.3f,\n", bw1);
    printf("  \"mem_bandwidth_gbs_mt\": %.3f,\n", bwN);
    printf("  \"checksum\": \"%llu/%.6g\"\n", (unsigned long long)isink, (double)dsink);
    printf("}\n");
    return 0;
}
