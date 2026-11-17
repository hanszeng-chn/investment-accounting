#!/usr/bin/env python3
"""Local ledger build, update, preview and encrypted Netlify publication."""
import argparse,json,subprocess,sys
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
DATA=BASE/'data/personal-investment'
def run(*args): subprocess.run(args,cwd=BASE,check=True)
p=argparse.ArgumentParser();p.add_argument('action',choices=['update','build','serve','deploy']);p.add_argument('--as-of');a=p.parse_args()
def ledger(command):
 args=[sys.executable,str(BASE/'ledger/scripts/ledger.py'),'--root',str(DATA),command]
 if a.as_of and command=='dashboard':args+=['--as-of',a.as_of]
 run(*args)
if a.action=='serve':run(sys.executable,str(BASE/'ledger/scripts/serve_dashboard.py'),'--root',str(DATA),'--port','8765')
else:
 if a.action=='update':
  run('node','tools/ledger-cloud.mjs','pull')
  args=[sys.executable,str(BASE/'ledger/scripts/quotes.py'),'--root',str(DATA)]
  if a.as_of:args+=['--as-of',a.as_of]
  run(*args)
 ledger('validate');ledger('dashboard')
 if a.action=='update':run('node','tools/ledger-cloud.mjs','push')
 if a.action=='deploy':
  run('node','tools/ledger-cloud.mjs','pull')
  ledger('dashboard')
  run('node','tools/encrypted-dashboard/build.mjs','--input',str(DATA/'dashboard/index.html'),'--output',str(BASE/'outputs/netlify-encrypted-dashboard'),'--password-file',str(DATA/'secrets/dashboard-password.txt'))
  site=json.loads((DATA/'netlify.json').read_text())['site_id']
  run('npx','--yes','netlify-cli','deploy','--prod','--no-build','--dir','outputs/netlify-encrypted-dashboard','--site',site)
