#!/usr/bin/env bash
set -euo pipefail

AMI_ID="ami-094f1b962b34950d5"
KEY_NAME="cloud"
KEY_PATH="/Users/harshithj/Main/Archive/OtherFiles/cloud.pem"
PROBE_SRC="/Users/harshithj/Main/Resources/CETP/scripts/collection/compute_probe.c"
OUT_DIR="/Users/harshithj/Main/Resources/CETP/scripts/collection/results"
mkdir -p "$OUT_DIR"

MACHINE_KEYS=(c5a m5a r5n z1d c7i)
MACHINE_TYPES=(c5a.xlarge m5a.xlarge r5n.large z1d.large c7i.large)

MY_IP=$(curl -s -4 ifconfig.me)

for idx in 0 1 2 3 4; do
  machine_key="${MACHINE_KEYS[$idx]}"
  itype="${MACHINE_TYPES[$idx]}"
  echo "=== Launching $machine_key ($itype) ==="

  instance_id=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$itype" \
    --key-name "$KEY_NAME" \
    --instance-market-options MarketType=spot \
    --query "Instances[0].InstanceId" --output text)

  echo "Instance ID: $instance_id"

  cleanup() {
    echo "Terminating $instance_id"
    aws ec2 terminate-instances --instance-ids "$instance_id" >/dev/null
  }
  trap cleanup EXIT

  echo "Waiting for instance to be running..."
  aws ec2 wait instance-running --instance-ids "$instance_id"
  sleep 45

  public_ip=$(aws ec2 describe-instances --instance-ids "$instance_id" \
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text)
  echo "Public IP: $public_ip"

  sg_id=$(aws ec2 describe-instances --instance-ids "$instance_id" \
    --query "Reservations[0].Instances[0].SecurityGroups[0].GroupId" --output text)
  aws ec2 authorize-security-group-ingress --group-id "$sg_id" \
    --protocol tcp --port 22 --cidr "${MY_IP}/32" 2>/dev/null || true

  ssh_opts="-i $KEY_PATH -o StrictHostKeyChecking=no -o ConnectTimeout=20"

  for attempt in $(seq 1 10); do
    if ssh $ssh_opts "ubuntu@${public_ip}" "echo ok" >/dev/null 2>&1; then
      break
    fi
    echo "SSH not ready yet, retrying ($attempt/10)..."
    sleep 15
  done

  scp $ssh_opts "$PROBE_SRC" "ubuntu@${public_ip}:/tmp/compute_probe.c"

  # IMDSv2. The original code issued a plain IMDSv1 GET, which this AMI's instances
  # refuse (token required), so it returned EMPTY on all five machines and instance
  # identity was never actually confirmed -- it was inferred from `nproc` matching the
  # expected vCPU count. That inference does not scale: at 30 machines several instance
  # types share a vCPU count, so nproc cannot identify them. Fetch a token, then use it.
  imds_cmd='TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 300") && \
    curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/instance-type'
  real_instance_type=$(ssh $ssh_opts "ubuntu@${public_ip}" "$imds_cmd")
  real_instance_id=$(ssh $ssh_opts "ubuntu@${public_ip}" \
    "${imds_cmd/instance-type/instance-id}")
  nproc_count=$(ssh $ssh_opts "ubuntu@${public_ip}" "nproc")

  if [ -z "$real_instance_type" ]; then
    echo "FATAL: IMDS returned no instance type for $machine_key. Refusing to record a" >&2
    echo "measurement that cannot be attributed to a confirmed instance." >&2
    exit 1
  fi
  if [ "$real_instance_type" != "$itype" ]; then
    echo "FATAL: requested $itype but IMDS reports $real_instance_type" >&2
    exit 1
  fi

  echo "Confirmed instance type: $real_instance_type (id $real_instance_id), nproc: $nproc_count"

  # Tier 1 item 27 established that probing at `nproc` is a definition bug: a PostgreSQL
  # query can only use 1 + max_parallel_workers_per_gather processes, so on a machine with
  # more cores than that the aggregate figure describes capacity no query can reach. It was
  # invisible at 2-4 vCPUs and catastrophic at 8 (324% vs 153% MAPE). Probe at BOTH the
  # achievable DOP and nproc, and record which is which.
  pg_dop=$(ssh $ssh_opts "ubuntu@${public_ip}" \
    "psql -tAc 'show max_parallel_workers_per_gather' 2>/dev/null || echo 2")
  dop_threads=$(( pg_dop + 1 ))
  remote_cmd="cd /tmp && gcc -O3 -march=native -o compute_probe compute_probe.c -lpthread && \
    echo '--- single-threaded ---' && taskset -c 1 ./compute_probe 1 15 42 && \
    echo '--- dop-matched (threads=${dop_threads}) ---' && ./compute_probe ${dop_threads} 15 42 && \
    echo '--- nproc (threads=${nproc_count}) ---' && ./compute_probe ${nproc_count} 15 42"

  output_file="${OUT_DIR}/${machine_key}_compute_benchmark.txt"
  ssh $ssh_opts "ubuntu@${public_ip}" "$remote_cmd" | tee "$output_file"

  echo "real_instance_type=${real_instance_type}" >> "$output_file"
  echo "nproc=${nproc_count}" >> "$output_file"

  trap - EXIT
  cleanup

  echo "=== Done with $machine_key ==="
  echo
done

echo "All 5 machines measured and terminated. Results in $OUT_DIR"
