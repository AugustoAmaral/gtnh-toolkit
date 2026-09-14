#!/bin/bash
PID=$(pgrep -f "lwjgl3ify-forgePatches.jar" | head -1)
HZ=$(getconf CLK_TCK)
read -r a b < <(awk "{print \$14, \$15}" /proc/$PID/stat)
T0=$(date +%s%N); sleep ${1:-90}
read -r c d < <(awk "{print \$14, \$15}" /proc/$PID/stat)
T1=$(date +%s%N)
CPU=$((c+d-a-b)); WALL=$(( (T1-T0)/1000000 ))
python3 -c "
cpu_ms=$CPU*1000.0/$HZ; wall=$WALL; mc=wall/50.0
print(f\"CPU={cpu_ms:.0f}ms wall={wall}ms core={100*cpu_ms/wall:.1f}%  CPU_PER_TICK={cpu_ms/mc:.2f}ms\")
"
