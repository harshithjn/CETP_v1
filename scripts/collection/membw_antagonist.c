/*
 * Memory-bandwidth antagonist for the CETP elasticity rig.
 *
 * Runs N threads, each streaming a STREAM-triad (a[i] = b[i] + s*c[i]) over a
 * per-thread working set deliberately far larger than any cache level, so the
 * loop is DRAM-bandwidth-bound rather than cache-resident. Co-running this
 * against PostgreSQL steals memory bandwidth without stealing much compute,
 * which is what lets runtime sensitivity to bandwidth be separated from
 * sensitivity to cores.
 *
 * Reports achieved aggregate bandwidth so the caller can verify it did real
 * work instead of being optimised away.
 *
 *   cc -O2 -o membw_antagonist membw_antagonist.c -lpthread
 *   ./membw_antagonist <threads> <seconds> [mib_per_thread]
 *
 * Portable: POSIX threads only. No taskset/cgroups, so it does not pin --
 * on a machine without affinity control (macOS) the OS spreads the threads,
 * which is sufficient to create contention but means the pressure is not
 * perfectly uniform. Recorded as a caveat by the rig.
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
    double *a, *b, *c;
    size_t n;
    volatile int *stop;
    double scalar;
    unsigned long long iters;
} work_t;

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static void *run(void *arg) {
    work_t *w = (work_t *)arg;
    unsigned long long it = 0;
    while (!*(w->stop)) {
        for (size_t i = 0; i < w->n; i++)
            w->a[i] = w->b[i] + w->scalar * w->c[i];
        it++;
        /* Defeat any attempt to hoist the loop: make the next pass depend on
         * a value the compiler cannot predict. */
        w->scalar += w->a[it % w->n] * 1e-300;
    }
    w->iters = it;
    return NULL;
}

int main(int argc, char **argv) {
    int nthreads = argc > 1 ? atoi(argv[1]) : 4;
    double seconds = argc > 2 ? atof(argv[2]) : 10.0;
    size_t mib = argc > 3 ? (size_t)atoll(argv[3]) : 48;
    if (nthreads <= 0) { printf("threads=0 bandwidth_gbs=0.00\n"); return 0; }

    size_t n = mib * 1024 * 1024 / sizeof(double) / 3;
    volatile int stop = 0;
    pthread_t *th = calloc(nthreads, sizeof(pthread_t));
    work_t *w = calloc(nthreads, sizeof(work_t));

    for (int t = 0; t < nthreads; t++) {
        w[t].n = n; w[t].stop = &stop; w[t].scalar = 3.0;
        w[t].a = malloc(n * sizeof(double));
        w[t].b = malloc(n * sizeof(double));
        w[t].c = malloc(n * sizeof(double));
        if (!w[t].a || !w[t].b || !w[t].c) { fprintf(stderr, "alloc failed\n"); return 1; }
        for (size_t i = 0; i < n; i++) { w[t].a[i] = 1.0; w[t].b[i] = 2.0; w[t].c[i] = 3.0; }
    }

    double t0 = now_s();
    for (int t = 0; t < nthreads; t++) pthread_create(&th[t], NULL, run, &w[t]);
    /* Sleep in short slices so the requested duration is honoured closely. */
    while (now_s() - t0 < seconds) {
        struct timespec ts = {0, 50 * 1000 * 1000};
        nanosleep(&ts, NULL);
    }
    stop = 1;
    unsigned long long total = 0;
    for (int t = 0; t < nthreads; t++) { pthread_join(th[t], NULL); total += w[t].iters; }
    double el = now_s() - t0;

    /* Triad touches 3 arrays per element: 2 reads + 1 write. */
    double bytes = (double)total * n * 3.0 * sizeof(double);
    printf("threads=%d seconds=%.2f mib_per_thread=%zu iters=%llu bandwidth_gbs=%.2f\n",
           nthreads, el, mib, total, bytes / el / 1e9);
    return 0;
}
