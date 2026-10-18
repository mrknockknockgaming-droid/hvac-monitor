#!/bin/bash
# Rebuild, autoroute and finish the carrier boards (Git Bash on the PC):
#   gen_pcb.py -> route.py export -> Freerouting -> route.py import (fill, ground fixes) -> DRC.
# Freerouting's result changes from run to run, so a board is redone (up to 6 tries) until
# kicad-cli DRC reports no errors and nothing unconnected. Writes <board>/drc.rpt.
#   ./route-all.sh [outdoor-carrier] [indoor-carrier]
cd "$(dirname "$0")"
K="/c/Program Files/KiCad/10.0/bin/python.exe"
CLI="/c/Program Files/KiCad/10.0/bin/kicad-cli.exe"
for b in ${@:-outdoor-carrier indoor-carrier}; do
  for try in 1 2 3 4 5 6; do
    "$K" scripts/gen_pcb.py $b > /dev/null 2>&1 || { echo "$b: gen_pcb failed"; exit 1; }
    "$K" scripts/route.py export $b 2>&1 | grep -v "image handler"
    java -jar tools/freerouting-2.1.0.jar -de $b/$b.dsn -do $b/$b.ses -mp 100 --gui.enabled=false 2>&1 \
      | grep -E "incomplete_count"
    "$K" scripts/route.py import $b 2>&1 | grep -v "image handler"
    "$CLI" pcb drc --severity-all --schematic-parity -o $b/drc.rpt $b/$b.kicad_pcb > /dev/null 2>&1
    if grep -q "Found 0 DRC violations" $b/drc.rpt && grep -q "Found 0 unconnected" $b/drc.rpt; then
      echo "$b: DRC clean on try $try"; break
    fi
    echo "$b: try $try not clean:"; grep -E "^\*\* Found" $b/drc.rpt
    [ $try = 6 ] && exit 1
  done
done
