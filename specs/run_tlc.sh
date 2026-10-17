#!/usr/bin/env bash
# Model-check every spec here. Fixed designs must pass; the models of the old
# algorithms must still be refuted (so the specs keep reproducing the bugs).
# Usage: specs/run_tlc.sh path/to/tla2tools.jar
set -uo pipefail
jar=$(cd "$(dirname "${1:?tla2tools.jar}")" && pwd)/$(basename "$1")
cd "$(dirname "$0")"
meta=$(mktemp -d); trap 'rm -rf "$meta"' EXIT
status=0
check() {  # module config expect(pass|violation)
  local out="$meta/$2.out"
  java -XX:+UseParallelGC -jar "$jar" -workers auto -metadir "$meta/$2" -config "$2.cfg" "$1.tla" > "$out" 2>&1
  local got=other
  grep -q 'Model checking completed. No error has been found.' "$out" && got=pass
  grep -qiE 'violated' "$out" && got=violation
  printf '%-22s expect %-9s got %-9s %s\n' "$2" "$3" "$got" "$(grep -oE '[0-9,]+ distinct states found' "$out" | tail -1)"
  if [ "$got" != "$3" ]; then status=1; tail -40 "$out"; fi
}
check MCCacheLeases CacheLeasesFixed   pass
check MCCacheLeases CacheLeasesBuggy   violation
check Deploy        Deploy_fixed_fresh pass
check Deploy        Deploy_fixed_stuck pass
check Deploy        Deploy_pr29_fresh  violation
check Deploy        Deploy_pr29_stuck  violation
check Deploy        Deploy_head_fresh  violation
check Deploy        Deploy_head_stuck  violation
rm -f ./*_TTrace_*
exit $status
