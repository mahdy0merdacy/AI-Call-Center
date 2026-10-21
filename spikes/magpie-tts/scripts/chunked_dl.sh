#!/bin/bash
# GitHub's CDN stalls on the full-file GET for this asset but serves ranged
# requests fine, so pull it in pieces and concatenate.
U=https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.1.0/nemo-speech-0.1.0-linux-x86_64-cuda.tar.gz
OUT=/tmp/nemo-cuda.tar.gz
TOTAL=107310946
CHUNK=$((8*1024*1024))
: > $OUT
off=0
while [ $off -lt $TOTAL ]; do
  end=$((off+CHUNK-1)); [ $end -ge $TOTAL ] && end=$((TOTAL-1))
  for try in 1 2 3 4 5; do
    if curl -sL -r "$off-$end" --max-time 300 -o /tmp/.part "$U"; then
      got=$(stat -c%s /tmp/.part 2>/dev/null || echo 0)
      if [ "$got" -eq $((end-off+1)) ]; then cat /tmp/.part >> $OUT; break; fi
    fi
    echo "retry $try for $off-$end"; sleep 3
  done
  off=$((end+1))
  echo "progress: $((off*100/TOTAL))%  ($((off/1048576)) / $((TOTAL/1048576)) MB)"
done
echo "DONE size=$(stat -c%s $OUT)"
