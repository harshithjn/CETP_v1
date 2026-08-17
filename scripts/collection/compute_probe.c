#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <pthread.h>

#define ARRAY_SIZE 2048
#define NUM_ACC 8

typedef struct {
    long iters;
    double seed;
    double result;
    double elapsed;
} thread_arg_t;

static double now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

static void *run_kernel(void *arg_ptr) {
    thread_arg_t *arg = (thread_arg_t *)arg_ptr;
    double data[ARRAY_SIZE];
    double acc[NUM_ACC];
    unsigned seed_bits = (unsigned)(arg->seed * 1000.0);
    for (int i = 0; i < ARRAY_SIZE; i++) {
        data[i] = 1.0 + 0.5 * (double)((i * 2654435761u + seed_bits) % 1000u) / 1000.0;
    }
    for (int j = 0; j < NUM_ACC; j++) acc[j] = 0.0;

    double t0 = now_seconds();
    for (long it = 0; it < arg->iters; it++) {
        data[it & (ARRAY_SIZE - 1)] += 1e-12;
        for (int i = 0; i < ARRAY_SIZE; i += NUM_ACC) {
            acc[0] = acc[0] + data[i + 0] * data[i + 0];
            acc[1] = acc[1] + data[i + 1] * data[i + 1];
            acc[2] = acc[2] + data[i + 2] * data[i + 2];
            acc[3] = acc[3] + data[i + 3] * data[i + 3];
            acc[4] = acc[4] + data[i + 4] * data[i + 4];
            acc[5] = acc[5] + data[i + 5] * data[i + 5];
            acc[6] = acc[6] + data[i + 6] * data[i + 6];
            acc[7] = acc[7] + data[i + 7] * data[i + 7];
        }
    }
    double t1 = now_seconds();

    double total = 0.0;
    for (int j = 0; j < NUM_ACC; j++) total += acc[j];
    arg->result = total;
    arg->elapsed = t1 - t0;
    return NULL;
}

static long calibrate_iters(double seed, double target_seconds) {
    thread_arg_t probe;
    long calib_iters = 200000L;
    probe.iters = calib_iters;
    probe.seed = seed;
    run_kernel(&probe);
    double per_iter = probe.elapsed / (double)calib_iters;
    long target_iters = (long)(target_seconds / per_iter);
    if (target_iters < calib_iters) target_iters = calib_iters;
    return target_iters;
}

int main(int argc, char **argv) {
    int threads_requested = argc > 1 ? atoi(argv[1]) : 1;
    double target_seconds = argc > 2 ? atof(argv[2]) : 15.0;
    double seed = argc > 3 ? atof(argv[3]) : (double)time(NULL);

    long iters = calibrate_iters(seed, target_seconds);

    pthread_t tids[threads_requested];
    thread_arg_t args[threads_requested];

    double t0 = now_seconds();
    for (int t = 0; t < threads_requested; t++) {
        args[t].iters = iters;
        args[t].seed = seed + (double)t;
        pthread_create(&tids[t], NULL, run_kernel, &args[t]);
    }
    for (int t = 0; t < threads_requested; t++) {
        pthread_join(tids[t], NULL);
    }
    double t1 = now_seconds();
    double wall = t1 - t0;

    double checksum = 0.0;
    double max_single_elapsed = 0.0;
    for (int t = 0; t < threads_requested; t++) {
        checksum += args[t].result;
        if (args[t].elapsed > max_single_elapsed) max_single_elapsed = args[t].elapsed;
    }

    long flops_per_thread = iters * (long)ARRAY_SIZE * 2L;
    long total_flops = flops_per_thread * (long)threads_requested;
    double gflops_aggregate = (double)total_flops / wall / 1e9;
    double gflops_per_thread = (double)flops_per_thread / max_single_elapsed / 1e9;

    printf("threads=%d iters=%ld array_size=%d target_seconds=%.1f\n", threads_requested, iters, ARRAY_SIZE, target_seconds);
    printf("wall_seconds=%.4f\n", wall);
    printf("checksum=%.6f\n", checksum);
    printf("total_flops=%ld\n", total_flops);
    printf("gflops_aggregate=%.4f\n", gflops_aggregate);
    printf("gflops_per_thread=%.4f\n", gflops_per_thread);

    if (gflops_per_thread < 0.3 || gflops_per_thread > 300.0) {
        printf("WARNING: gflops_per_thread=%.4f is outside the physically plausible range [0.3, 300] "
               "for a single core's double-precision throughput; suspect compiler elimination or a timing bug.\n",
               gflops_per_thread);
    } else {
        printf("PLAUSIBILITY CHECK PASSED: gflops_per_thread within [0.3, 300] GFLOPS.\n");
    }

    return 0;
}
