#!/usr/bin/env python3
"""Serve the generated dashboard read-only on loopback; no ledger mutation routes."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import ledger

def make_server(root,port=8765):
    root=Path(root).resolve()
    class Handler(BaseHTTPRequestHandler):
        def reply(self,status,body,kind='application/json; charset=utf-8'):
            raw=body.encode();self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(raw)
        def valid_host(self):return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'
        def do_GET(self):
            if not self.valid_host():return self.reply(403,'{}')
            if self.path not in ('/','/index.html'):return self.reply(404,'{}')
            try:
                body=(root/'dashboard'/'index.html').read_text()
                self.reply(200,body,'text/html; charset=utf-8')
            except (ValueError,OSError,KeyError) as e:self.reply(400,json.dumps({'error':str(e)}))
        def do_POST(self):
            self.reply(405,json.dumps({'error':'Read-only dashboard'}))
        do_PUT=do_POST
        do_PATCH=do_POST
        do_DELETE=do_POST
        def log_message(self,*args):pass
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default=json.loads((ledger.SKILL/'settings.json').read_text())['data_dir']);p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    server=make_server(a.root,a.port);print(f'http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
